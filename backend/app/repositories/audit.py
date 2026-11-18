from __future__ import annotations
import json
import sqlite3
from datetime import datetime
from typing import Any


def log(
    conn: sqlite3.Connection,
    action: str,
    target_type: str,
    target_id: int,
    detail: dict[str, Any] | None = None,
    actor: str = "",
) -> int:
    cur = conn.execute(
        "INSERT INTO audit_log(action, target_type, target_id, detail, actor, created_at) "
        "VALUES(?,?,?,?,?,?)",
        (
            action,
            target_type,
            int(target_id),
            json.dumps(detail or {}, ensure_ascii=False, default=str),
            actor or "",
            datetime.now().replace(microsecond=0).isoformat(),
        ),
    )
    return int(cur.lastrowid)


def list_entries(
    conn: sqlite3.Connection,
    target_type: str | None = None,
    target_id: int | None = None,
) -> list[dict[str, Any]]:
    where: list[str] = []
    args: list[Any] = []
    if target_type:
        where.append("target_type=?")
        args.append(target_type)
    if target_id is not None:
        where.append("target_id=?")
        args.append(int(target_id))
    sql = "SELECT * FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC"
    rows = conn.execute(sql, args).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        entry = dict(r)
        try:
            entry["detail"] = json.loads(entry.get("detail") or "{}")
        except (TypeError, ValueError):
            pass
        out.append(entry)
    return out
