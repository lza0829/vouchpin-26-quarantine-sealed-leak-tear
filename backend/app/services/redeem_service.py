from __future__ import annotations
from datetime import datetime
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import batches as batch_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import audit as audit_repo
from app.services import quarantine_bridge
from app.services import redeem_validator


def list_facts(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect(immediate=True)
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        # Facts are never shown in a state that contradicts the quarantine
        # table: a torn half row is rolled back before it can be read.
        quarantine_bridge.reconcile_batches(conn)
        quarantine_bridge.reconcile_day(conn, day)
        rows = fact_repo.list_facts(conn, day)
        conn.commit()
        return rows
    finally:
        conn.close()


def preview(business_day: str | None = None) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        # Heal first: the preview is the operator's view of what a seal would
        # pin, so a fake promotion or a half formal row must be settled before
        # it is either shown or counted as excluded.
        quarantine_bridge.reconcile_batches(conn)
        quarantine_bridge.reconcile_day(conn, day)
        opens, quarantined = quarantine_bridge.pinnable(conn, day)
        excluded = quarantine_bridge.excluded_summary(conn, day)
        total = round(sum(float(f["amount"]) for f in opens), 2)
        q = quarantine_repo.counts_for_day(conn, day)
        result = {
            "business_day": day,
            "open_count": len(opens),
            "open_total": total,
            "already_sealed": fact_repo.day_is_sealed(conn, day),
            "excluded_quarantine_count": excluded["count"],
            "excluded_quarantine_amount": excluded["amount"],
            "quarantine_still": q["quarantine_count"],
            "facts": opens,
        }
        conn.commit()
        return result
    finally:
        conn.close()



def seal(body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        actor = (body.get("actor") or "").strip()
        # Reconcile first: the quarantine table is authoritative, so any row
        # still quarantined is excluded, and any fake promotion is rolled back
        # before it can leak a half formal row into this batch. Torn batches
        # from an interrupted seal release their rows and never count as the
        # current sealed batch.
        batches_fixed = quarantine_bridge.reconcile_batches(conn, actor)
        fixed = quarantine_bridge.reconcile_day(conn, day, actor)
        if fact_repo.day_is_sealed(conn, day):
            raise HTTPException(409, f"业务日 {day} 已有 sealed 钉扎批")
        opens, quarantined = quarantine_bridge.pinnable(conn, day)
        if not opens:
            raise HTTPException(400, "无可钉扎的 open 核销事实")
        bid = batch_repo.create_batch(
            conn,
            business_day=day,
            status="pending",
            fact_count=0,
            total_amount=0.0,
            sealed_at=None,
            note=body.get("note") or "",
        )
        pinned = fact_repo.mark_batched(conn, [int(f["id"]) for f in opens], bid)
        sealed_rows = fact_repo.facts_for_batch(conn, bid)
        # The header is written from the rows actually pinned, never from the
        # pre-pin preview, so a half-finished batch can never claim counts it
        # does not own.
        fact_count = int(pinned)
        total = round(sum(float(f["amount"]) for f in sealed_rows), 2)
        if pinned != len(opens) or len(sealed_rows) != pinned:
            raise HTTPException(409, "钉扎期间 open 事实发生变化，请重试")
        batch_repo.update_header(conn, bid, fact_count=fact_count, total_amount=total)
        batch_repo.set_status(conn, bid, "sealed")
        conn.execute(
            "UPDATE redeem_batches SET sealed_at=? WHERE id=?",
            (datetime.now().replace(microsecond=0).isoformat(), bid),
        )
        # Invariant at commit time: no still-quarantined row shares an event
        # with a pinned fact, and the header equals the sum of its own rows.
        pinned_events = {int(f["event_id"]) for f in sealed_rows}
        for q in quarantined:
            if int(q["event_id"]) in pinned_events:  # pragma: no cover - reconcile is the gate
                raise HTTPException(409, f"隔离行 #{q['quarantine_id']} 与正式事实双栖，拒绝钉扎")
        audit_repo.log(
            conn,
            action="seal",
            target_type="batch",
            target_id=bid,
            detail={
                "business_day": day,
                "fact_count": fact_count,
                "total_amount": total,
                "excluded_quarantine_count": len(quarantined),
                **fixed,
                **batches_fixed,
            },
            actor=actor,
        )
        conn.commit()
        return {
            "batch_id": bid,
            "business_day": day,
            "fact_count": fact_count,
            "total_amount": total,
            "status": "sealed",
            "excluded_quarantine_count": len(quarantined),
            "reconciled": {**fixed, **batches_fixed},
        }
    finally:
        conn.close()



def list_batches() -> list[dict[str, Any]]:
    # Heal torn batches on read: a batch that never reached ``sealed`` must not
    # show up as the current pinned batch, and every header is re-derived from
    # the rows it owns.
    conn = connect(immediate=True)
    try:
        quarantine_bridge.reconcile_batches(conn)
        rows = batch_repo.list_batches(conn)
        conn.commit()
        return rows
    finally:
        conn.close()


def list_quarantine(business_day: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    if status and status not in quarantine_repo.STATUSES:
        raise HTTPException(400, f"status 须为 {'/'.join(quarantine_repo.STATUSES)}")
    conn = connect(immediate=True)
    try:
        # The quarantine page must not tell the operator a row is pending when
        # the formal fact already exists (or claim success with no fact), so
        # the same reconciliation the seal runs is applied before listing.
        quarantine_bridge.reconcile_batches(conn)
        day = business_day or settings_repo.get(conn, "business_day")
        quarantine_bridge.reconcile_day(conn, day)
        rows = quarantine_repo.list_rows(conn, business_day, status)
        for r in rows:
            r["reason_label"] = redeem_validator.REASON_LABELS.get(r["reason_code"], r["reason_code"])
        conn.commit()
        return rows
    finally:
        conn.close()


def promote(quarantine_id: int, body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        row = quarantine_repo.get(conn, quarantine_id)
        if row is None:
            raise HTTPException(404, "隔离行不存在")
        day = str(row["business_day"])
        # Heal a torn state (a fake promotion receipt, a half-written formal
        # row on an event this table still owns, or a batch stuck before
        # ``sealed`` that must not count as today's pin) before promoting, so
        # the decision cannot land on top of a second home for the same key.
        quarantine_bridge.reconcile_batches(conn, (body.get("actor") or "").strip())
        quarantine_bridge.reconcile_day(conn, day, (body.get("actor") or "").strip())
        row = quarantine_repo.get(conn, quarantine_id)
        if row is None:
            raise HTTPException(404, "隔离行不存在")
        if row["status"] != "quarantined":
            raise HTTPException(409, f"隔离行当前状态为 {row['status']}，不可晋升")
        if fact_repo.day_is_sealed(conn, day):
            raise HTTPException(409, f"业务日 {day} 已钉扎，隔离行不得晋升")

        try:
            amount = row["amount"] if body.get("amount") is None else float(body["amount"])
        except (TypeError, ValueError) as e:
            raise HTTPException(400, "amount 须为数字") from e
        campaign_code = ((body.get("campaign_code") or "").strip()) or str(row["campaign_code"])
        reason_code, reason = redeem_validator.validate_redeem(
            conn,
            coupon_code=str(row["coupon_code"]),
            campaign_code=campaign_code,
            amount=float(amount),
            business_day=day,
            quarantine_id=quarantine_id,
        )
        if reason_code is not None:
            raise HTTPException(
                409,
                f"晋升校验未通过: [{reason_code}] {reason}",
            )

        fact_id = quarantine_bridge.promote_to_fact(
            conn,
            row,
            amount=float(amount),
            campaign_code=campaign_code,
        )
        audit_repo.log(
            conn,
            action="promote",
            target_type="quarantine",
            target_id=quarantine_id,
            detail={
                "event_id": int(row["event_id"]),
                "fact_id": fact_id,
                "business_day": day,
                "coupon_code": row["coupon_code"],
                "claimed_status": "promoted",
                "note": body.get("note") or "",
            },
            actor=(body.get("actor") or "").strip(),
        )
        conn.commit()
        return {
            "quarantine_id": quarantine_id,
            "fact_id": fact_id,
            "business_day": day,
            "status": "promoted",
            "success": True,
            "quarantine_status": "promoted",
        }
    finally:
        conn.close()


def discard(quarantine_id: int, body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        row = quarantine_repo.get(conn, quarantine_id)
        if row is None:
            raise HTTPException(404, "隔离行不存在")
        if row["status"] != "quarantined":
            raise HTTPException(409, f"隔离行当前状态为 {row['status']}，不可丢弃")
        quarantine_repo.mark_discarded(conn, quarantine_id, body.get("note") or "")
        audit_repo.log(
            conn,
            action="discard",
            target_type="quarantine",
            target_id=quarantine_id,
            detail={
                "event_id": int(row["event_id"]),
                "business_day": row["business_day"],
                "coupon_code": row["coupon_code"],
                "reason_code": row["reason_code"],
                "note": body.get("note") or "",
            },
            actor=(body.get("actor") or "").strip(),
        )
        conn.commit()
        return {"quarantine_id": quarantine_id, "status": "discarded"}
    finally:
        conn.close()


def list_audit(target_type: str | None = None, target_id: int | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        return audit_repo.list_entries(conn, target_type, target_id)
    finally:
        conn.close()
