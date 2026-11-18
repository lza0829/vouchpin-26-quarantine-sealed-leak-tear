from __future__ import annotations
import sqlite3
from typing import Any


def list_events(conn: sqlite3.Connection, business_day: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT e.*, q.id AS quarantine_id, q.status AS quarantine_status "
        "FROM coupon_events e "
        "LEFT JOIN redeem_quarantine q ON q.event_id = e.id "
    )
    if business_day:
        rows = conn.execute(sql + "WHERE e.business_day=? ORDER BY e.id", (business_day,)).fetchall()
    else:
        rows = conn.execute(sql + "ORDER BY e.id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def insert_event(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO coupon_events(event_type, coupon_code, campaign_code, amount, business_day, occurred_at, note, reason_code) VALUES(?,?,?,?,?,?,?,?)",
        (
            fields["event_type"],
            fields["coupon_code"],
            fields["campaign_code"],
            float(fields.get("amount") or 0),
            fields["business_day"],
            fields["occurred_at"],
            fields.get("note") or "",
            fields.get("reason_code") or "",
        ),
    )
    return int(cur.lastrowid)


def set_reason_code(conn: sqlite3.Connection, event_id: int, reason_code: str) -> None:
    conn.execute(
        "UPDATE coupon_events SET reason_code=? WHERE id=?",
        (reason_code or "", int(event_id)),
    )


def issue_campaigns(conn: sqlite3.Connection, coupon_code: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT campaign_code FROM coupon_events WHERE event_type='issue' AND coupon_code=?",
        (coupon_code,),
    ).fetchall()
    return [str(r["campaign_code"]) for r in rows]


def has_event(conn: sqlite3.Connection, event_type: str, coupon_code: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM coupon_events WHERE event_type=? AND coupon_code=? LIMIT 1",
        (event_type, coupon_code),
    ).fetchone()
    return row is not None
