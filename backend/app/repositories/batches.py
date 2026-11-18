from __future__ import annotations
import sqlite3
from typing import Any


def list_batches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM redeem_batches ORDER BY id DESC").fetchall()]


def create_batch(conn: sqlite3.Connection, **fields: Any) -> int:
    cur = conn.execute(
        "INSERT INTO redeem_batches(business_day, status, fact_count, total_amount, sealed_at, note) VALUES(?,?,?,?,?,?)",
        (
            fields["business_day"],
            fields.get("status") or "sealed",
            int(fields.get("fact_count") or 0),
            float(fields.get("total_amount") or 0),
            fields.get("sealed_at"),
            fields.get("note") or "",
        ),
    )
    return int(cur.lastrowid)


def get(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM redeem_batches WHERE id=?", (int(batch_id),)).fetchone()
    return dict(row) if row else None


def list_for_day(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM redeem_batches WHERE business_day=? ORDER BY id",
        (business_day,),
    ).fetchall()
    return [dict(r) for r in rows]


def update_header(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    fact_count: int,
    total_amount: float,
    note: str | None = None,
) -> None:
    if note is None:
        conn.execute(
            "UPDATE redeem_batches SET fact_count=?, total_amount=? WHERE id=?",
            (int(fact_count), float(total_amount), int(batch_id)),
        )
    else:
        conn.execute(
            "UPDATE redeem_batches SET fact_count=?, total_amount=?, note=? WHERE id=?",
            (int(fact_count), float(total_amount), note, int(batch_id)),
        )


def set_status(conn: sqlite3.Connection, batch_id: int, status: str) -> int:
    cur = conn.execute(
        "UPDATE redeem_batches SET status=? WHERE id=?",
        (status, int(batch_id)),
    )
    return int(cur.rowcount or 0)

