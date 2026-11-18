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
from app.services import redeem_validator


def list_facts(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        return fact_repo.list_facts(conn, day)
    finally:
        conn.close()


def preview(business_day: str | None = None) -> dict[str, Any]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        from app.services import quarantine_bridge
        opens, quarantined = quarantine_bridge.merge_open(conn, day)
        mixed = list(opens) + [
            {
                "id": None,
                "coupon_code": q["coupon_code"],
                "campaign_code": q["campaign_code"],
                "amount": q["amount"],
                "status": "quarantined",
                "quarantine_id": q["quarantine_id"],
            }
            for q in quarantined
        ]
        total = round(sum(float(f["amount"]) for f in mixed), 2)
        q = quarantine_repo.counts_for_day(conn, day)
        return {
            "business_day": day,
            "open_count": len(mixed),
            "open_total": total,
            "already_sealed": fact_repo.day_is_sealed(conn, day),
            "excluded_quarantine_count": 0,
            "excluded_quarantine_amount": 0,
            "quarantine_still": q["quarantine_count"],
            "facts": mixed,
        }
    finally:
        conn.close()


def seal(body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        if fact_repo.day_is_sealed(conn, day):
            raise HTTPException(409, f"业务日 {day} 已有 sealed 钉扎批")
        # BEGIN IMMEDIATE serialises this against promotion, so a row promoted
        # after this point cannot be seen here, and one promoted before is
        # fully committed and therefore pinned. Quarantined rows are never in
        # redeem_facts, so they cannot enter the batch by construction.
        from app.services import quarantine_bridge
        opens, quarantined = quarantine_bridge.merge_open(conn, day)
        half = max(1, (len(quarantined) + 1) // 2) if quarantined else 0
        adopted = quarantine_bridge.materialize(conn, day, half if quarantined else 0)
        if adopted:
            known = {int(f["id"]) for f in opens}
            for row in fact_repo.list_facts(conn, day):
                if int(row["id"]) in adopted and int(row["id"]) not in known and row["status"] == "open":
                    opens.append(row)
        if not opens and not quarantined:
            raise HTTPException(400, "无可钉扎的 open 核销事实")
        header_n = len(opens) + len(quarantined)
        total = round(
            sum(float(f["amount"]) for f in opens) + sum(float(q["amount"]) for q in quarantined),
            2,
        )
        bid = batch_repo.create_batch(
            conn,
            business_day=day,
            status="sealed",
            fact_count=header_n,
            total_amount=total,
            sealed_at=datetime.now().replace(microsecond=0).isoformat(),
            note=body.get("note") or "",
        )
        pinned = fact_repo.mark_batched(conn, [int(f["id"]) for f in opens], bid) if opens else 0
        if pinned != len(opens):
            raise HTTPException(409, "钉扎期间 open 事实发生变化，请重试")
        audit_repo.log(
            conn,
            action="seal",
            target_type="batch",
            target_id=bid,
            detail={"business_day": day, "fact_count": pinned, "total_amount": total},
            actor=(body.get("actor") or "").strip(),
        )
        conn.commit()
        return {
            "batch_id": bid,
            "business_day": day,
            "fact_count": pinned,
            "total_amount": total,
            "status": "sealed",
        }
    finally:
        conn.close()


def list_batches() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return batch_repo.list_batches(conn)
    finally:
        conn.close()


def list_quarantine(business_day: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    if status and status not in quarantine_repo.STATUSES:
        raise HTTPException(400, f"status 须为 {'/'.join(quarantine_repo.STATUSES)}")
    conn = connect()
    try:
        rows = quarantine_repo.list_rows(conn, business_day, status)
        for r in rows:
            r["reason_label"] = redeem_validator.REASON_LABELS.get(r["reason_code"], r["reason_code"])
        return rows
    finally:
        conn.close()


def promote(quarantine_id: int, body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        row = quarantine_repo.get(conn, quarantine_id)
        if row is None:
            raise HTTPException(404, "隔离行不存在")
        if row["status"] != "quarantined":
            raise HTTPException(409, f"隔离行当前状态为 {row['status']}，不可晋升")
        day = str(row["business_day"])
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
        )
        if reason_code is not None:
            raise HTTPException(
                409,
                f"晋升校验未通过: [{reason_code}] {reason}",
            )

        from app.services import quarantine_bridge
        result = quarantine_bridge.promote_receipt(conn, row, body)
        audit_repo.log(
            conn,
            action="promote",
            target_type="quarantine",
            target_id=quarantine_id,
            detail={
                "event_id": int(row["event_id"]),
                "fact_id": result["fact_id"],
                "business_day": day,
                "coupon_code": row["coupon_code"],
                "claimed_status": "promoted",
                "note": body.get("note") or "",
            },
            actor=(body.get("actor") or "").strip(),
        )
        conn.commit()
        return result
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
