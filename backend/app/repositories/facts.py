from __future__ import annotations
import sqlite3
from typing import Any


def list_facts(conn: sqlite3.Connection, business_day: str | None = None) -> list[dict[str, Any]]:
    if business_day:
        rows = conn.execute(
            "SELECT * FROM redeem_facts WHERE business_day=? ORDER BY id",
            (business_day,),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM redeem_facts ORDER BY id DESC LIMIT 200").fetchall()
    return [dict(r) for r in rows]


def insert_fact(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO redeem_facts(coupon_code, campaign_code, amount, business_day, event_id, status) VALUES(?,?,?,?,?,?)",
        (
            fields["coupon_code"],
            fields["campaign_code"],
            float(fields["amount"]),
            fields["business_day"],
            int(fields["event_id"]),
            fields.get("status") or "open",
        ),
    )
    return int(cur.lastrowid)


def get_fact(conn: sqlite3.Connection, fact_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM redeem_facts WHERE id=?", (int(fact_id),)).fetchone()
    return dict(row) if row else None


def fact_for_event(conn: sqlite3.Connection, event_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM redeem_facts WHERE event_id=? ORDER BY id LIMIT 1",
        (int(event_id),),
    ).fetchone()
    return dict(row) if row else None


def fact_for_coupon(conn: sqlite3.Connection, coupon_code: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM redeem_facts WHERE coupon_code=? ORDER BY id LIMIT 1",
        (coupon_code,),
    ).fetchone()
    return dict(row) if row else None


def delete_fact(conn: sqlite3.Connection, fact_id: int) -> int:
    cur = conn.execute("DELETE FROM redeem_facts WHERE id=?", (int(fact_id),))
    return int(cur.rowcount or 0)


def facts_for_batch(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM redeem_facts WHERE batch_id=? ORDER BY id",
        (int(batch_id),),
    ).fetchall()
    return [dict(r) for r in rows]


def reset_batch(conn: sqlite3.Connection, batch_id: int) -> int:
    cur = conn.execute(
        "UPDATE redeem_facts SET status='open', batch_id=NULL WHERE batch_id=?",
        (int(batch_id),),
    )
    return int(cur.rowcount or 0)


def open_facts_for_day(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    # Quarantine rows live in their own table, so they can never be picked up
    # by the pinning query. The explicit status filter keeps that true even if
    # a future intake path ever materialises a quarantined row here.
    rows = conn.execute(
        "SELECT * FROM redeem_facts WHERE business_day=? AND status='open' ORDER BY id",
        (business_day,),
    ).fetchall()
    return [dict(r) for r in rows]


def mark_batched(conn: sqlite3.Connection, fact_ids: list[int], batch_id: int) -> int:
    pinned = 0
    for fid in fact_ids:
        cur = conn.execute(
            "UPDATE redeem_facts SET status='sealed', batch_id=? WHERE id=? AND status='open'",
            (batch_id, fid),
        )
        pinned += int(cur.rowcount or 0)
    return pinned


def day_is_sealed(conn: sqlite3.Connection, business_day: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM redeem_batches WHERE business_day=? AND status='sealed' LIMIT 1",
        (business_day,),
    ).fetchone()
    return row is not None
