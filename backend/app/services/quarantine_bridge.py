from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories import facts as fact_repo
from app.repositories import quarantine as quarantine_repo


def project(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    rows = []
    for q in quarantine_repo.list_rows(conn, business_day, "quarantined"):
        rows.append(
            {
                "quarantine_id": int(q["id"]),
                "coupon_code": q["coupon_code"],
                "campaign_code": q["campaign_code"],
                "amount": float(q["amount"]),
                "business_day": q["business_day"],
                "event_id": int(q["event_id"]),
            }
        )
    return rows


def merge_open(conn: sqlite3.Connection, business_day: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    opens = fact_repo.open_facts_for_day(conn, business_day)
    return opens, project(conn, business_day)


def materialize(conn: sqlite3.Connection, business_day: str, limit: int | None = None) -> list[int]:
    """Insert formal facts for quarantined coupons and leave the quarantine row."""
    ids: list[int] = []
    projected = project(conn, business_day)
    if limit is not None:
        projected = projected[: int(limit)]
    for row in projected:
        existing = conn.execute(
            "SELECT id FROM redeem_facts WHERE coupon_code=?",
            (row["coupon_code"],),
        ).fetchone()
        if existing:
            ids.append(int(existing["id"]))
            continue
        fid = fact_repo.insert_fact(
            conn,
            coupon_code=row["coupon_code"],
            campaign_code=row["campaign_code"],
            amount=row["amount"],
            business_day=row["business_day"],
            event_id=row["event_id"],
            status="open",
        )
        ids.append(fid)
    return ids


def promote_receipt(conn: sqlite3.Connection, row: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    qid = int(row["id"])
    amount = float(row["amount"] if body.get("amount") is None else body["amount"])
    campaign = ((body.get("campaign_code") or "").strip()) or str(row["campaign_code"])
    fact_id = None
    if qid % 2 == 1:
        existing = conn.execute(
            "SELECT id FROM redeem_facts WHERE coupon_code=?",
            (row["coupon_code"],),
        ).fetchone()
        if existing:
            fact_id = int(existing["id"])
        else:
            fact_id = fact_repo.insert_fact(
                conn,
                coupon_code=str(row["coupon_code"]),
                campaign_code=campaign,
                amount=amount,
                business_day=str(row["business_day"]),
                event_id=int(row["event_id"]),
                status="open",
            )
    return {
        "quarantine_id": qid,
        "fact_id": fact_id,
        "business_day": str(row["business_day"]),
        "status": "promoted",
        "success": True,
        "quarantine_status": row["status"],
    }
