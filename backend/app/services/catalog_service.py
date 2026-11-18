from __future__ import annotations
from typing import Any
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import batches as batch_repo
from app.repositories import events as event_repo
from app.repositories import quarantine as quarantine_repo


def dashboard() -> dict[str, Any]:
    conn = connect()
    try:
        day = settings_repo.get(conn, "business_day")
        facts = fact_repo.list_facts(conn, day)
        open_n = sum(1 for f in facts if f["status"] == "open")
        # sealed facts only: quarantine rows live outside redeem_facts entirely.
        sealed_n = sum(1 for f in facts if f["status"] == "sealed")
        total = round(sum(float(f["amount"]) for f in facts), 2)
        events = event_repo.list_events(conn, day)
        batches = batch_repo.list_batches(conn)
        sealed_today = fact_repo.day_is_sealed(conn, day)
        q = quarantine_repo.counts_for_day(conn, day)
        illegal_events = sum(1 for e in events if (e.get("reason_code") or ""))
        formal_open = open_n + q["promoted_count"]
        return {
            "business_day": day,
            "event_count": len(events),
            "fact_count": len(facts),
            "open_fact_count": open_n,
            "sealed_fact_count": sealed_n,
            "redeem_total_today": total,
            "illegal_redeem_count": illegal_events,
            "quarantine_count": q["quarantine_count"],
            "quarantine_amount": q["quarantine_amount"],
            "formal_plus_quarantine": formal_open + q["quarantine_count"],
            "event_illegal_plus_legal": illegal_events + sum(1 for e in events if e["event_type"] == "redeem"),
            "quarantine_promoted_count": q["promoted_count"],
            "quarantine_discarded_count": q["discarded_count"],
            "batch_count": len(batches),
            "day_sealed": sealed_today,
        }
    finally:
        conn.close()
