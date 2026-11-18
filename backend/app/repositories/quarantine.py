from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from typing import Any

STATUSES = ("quarantined", "promoted", "discarded")


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def insert_quarantine(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO redeem_quarantine("
        "event_id, coupon_code, campaign_code, amount, business_day, occurred_at, "
        "reason_code, reason, status, payload, created_at"
        ") VALUES(?,?,?,?,?,?,?,?, 'quarantined', ?, ?)",
        (
            int(fields["event_id"]),
            fields["coupon_code"],
            fields["campaign_code"],
            float(fields.get("amount") or 0),
            fields["business_day"],
            fields["occurred_at"],
            fields["reason_code"],
            fields.get("reason") or "",
            json.dumps(fields.get("payload") or {}, ensure_ascii=False, default=str),
            fields.get("created_at") or _now(),
        ),
    )
    return int(cur.lastrowid)


def get(conn: sqlite3.Connection, quarantine_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM redeem_quarantine WHERE id=?", (int(quarantine_id),)).fetchone()
    return dict(row) if row else None


def list_rows(
    conn: sqlite3.Connection,
    business_day: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if business_day:
        where.append("business_day=?")
        args.append(business_day)
    if status:
        where.append("status=?")
        args.append(status)
    sql = "SELECT * FROM redeem_quarantine"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def counts_for_day(conn: sqlite3.Connection, business_day: str) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n, COALESCE(SUM(amount), 0) AS amount "
        "FROM redeem_quarantine WHERE business_day=? GROUP BY status",
        (business_day,),
    ).fetchall()
    by_status = {str(r["status"]): {"count": int(r["n"]), "amount": round(float(r["amount"]), 2)} for r in rows}
    quarantined = by_status.get("quarantined", {"count": 0, "amount": 0.0})
    return {
        "quarantine_count": quarantined["count"],
        "quarantine_amount": quarantined["amount"],
        "promoted_count": by_status.get("promoted", {"count": 0})["count"],
        "discarded_count": by_status.get("discarded", {"count": 0})["count"],
    }


def mark_promoted(conn: sqlite3.Connection, quarantine_id: int, fact_id: int, note: str) -> None:
    conn.execute(
        "UPDATE redeem_quarantine SET status='promoted', fact_id=?, decision_note=?, "
        "decided_at=? WHERE id=? AND status='quarantined'",
        (int(fact_id), note or "", _now(), int(quarantine_id)),
    )


def mark_discarded(conn: sqlite3.Connection, quarantine_id: int, note: str) -> None:
    conn.execute(
        "UPDATE redeem_quarantine SET status='discarded', decision_note=?, "
        "decided_at=? WHERE id=? AND status='quarantined'",
        (note or "", _now(), int(quarantine_id)),
    )
