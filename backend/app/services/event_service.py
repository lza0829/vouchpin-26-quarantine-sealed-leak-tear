from __future__ import annotations
from datetime import datetime
from typing import Any
from fastapi import HTTPException
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import events as event_repo
from app.repositories import facts as fact_repo
from app.repositories import campaigns as camp_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import audit as audit_repo
from app.services import redeem_validator


def list_campaigns() -> list[dict[str, Any]]:
    conn = connect()
    try:
        return camp_repo.list_all(conn)
    finally:
        conn.close()


def list_events(business_day: str | None = None) -> list[dict[str, Any]]:
    conn = connect()
    try:
        day = business_day or settings_repo.get(conn, "business_day")
        return event_repo.list_events(conn, day)
    finally:
        conn.close()


def add_event(body: dict[str, Any]) -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        day = body.get("business_day") or settings_repo.get(conn, "business_day")
        et = body.get("event_type")
        if et not in {"issue", "redeem", "void"}:
            raise HTTPException(400, "event_type 须为 issue/redeem/void")
        code = (body.get("coupon_code") or "").strip()
        camp = (body.get("campaign_code") or "").strip()
        if not code:
            raise HTTPException(400, "coupon_code 必填")
        if et != "redeem" and not camp:
            raise HTTPException(400, "coupon_code / campaign_code 必填")

        raw_amount = body.get("amount", None)
        try:
            amount = 0.0 if raw_amount is None or raw_amount == "" else float(raw_amount)
        except (TypeError, ValueError) as e:
            raise HTTPException(400, "amount 须为数字") from e
        if et == "redeem" and (raw_amount is None or raw_amount == ""):
            # An omitted amount falls back to the campaign face value; only an
            # explicitly supplied non-positive amount is treated as illegal.
            face = redeem_validator.face_value(conn, camp)
            if face is not None:
                amount = face

        occurred = body.get("occurred_at") or datetime.now().replace(microsecond=0).isoformat()
        note = body.get("note") or ""

        reason_code = None
        reason = ""
        if et == "redeem":
            reason_code, reason = redeem_validator.validate_redeem(
                conn,
                coupon_code=code,
                campaign_code=camp,
                amount=amount,
                business_day=day,
            )

        eid = event_repo.insert_event(
            conn,
            event_type=et,
            coupon_code=code,
            campaign_code=camp,
            amount=amount,
            business_day=day,
            occurred_at=occurred,
            note=note,
            reason_code=reason_code or "",
        )

        if et != "redeem":
            conn.commit()
            return {"event_id": eid, "fact_id": None, "business_day": day, "status": "accepted"}

        if reason_code is None:
            try:
                fact_id = fact_repo.insert_fact(
                    conn,
                    coupon_code=code,
                    campaign_code=camp,
                    amount=amount,
                    business_day=day,
                    event_id=eid,
                    status="open",
                )
            except Exception as e:
                raise HTTPException(409, f"核销事实写入失败（可能券码已核销）: {e}") from e
            conn.commit()
            return {
                "event_id": eid,
                "fact_id": fact_id,
                "business_day": day,
                "status": "accepted",
            }

        # Illegal redeem: the event stays in the lake for audit, the row goes
        # to quarantine, and no fact is materialised, so pinning never sees it.
        qid = quarantine_repo.insert_quarantine(
            conn,
            event_id=eid,
            coupon_code=code,
            campaign_code=camp,
            amount=amount,
            business_day=day,
            occurred_at=occurred,
            reason_code=reason_code,
            reason=reason,
            payload=body,
        )
        audit_repo.log(
            conn,
            action="quarantine",
            target_type="quarantine",
            target_id=qid,
            detail={
                "event_id": eid,
                "business_day": day,
                "coupon_code": code,
                "campaign_code": camp,
                "amount": amount,
                "reason_code": reason_code,
                "reason": reason,
            },
            actor=(body.get("actor") or "").strip(),
        )
        conn.commit()
        return {
            "event_id": eid,
            "fact_id": None,
            "quarantine_id": qid,
            "business_day": day,
            "status": "quarantined",
            "reason_code": reason_code,
            "reason": reason,
        }
    finally:
        conn.close()
