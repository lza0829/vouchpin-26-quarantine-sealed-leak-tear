from __future__ import annotations
import sqlite3
from typing import Any


def list_all(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute("SELECT * FROM campaigns ORDER BY id").fetchall()]


def get_by_code(conn: sqlite3.Connection, code: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM campaigns WHERE code=?", (code,)).fetchone()
    return dict(row) if row else None
