from __future__ import annotations
from typing import Any
from app.db import connect
from app.repositories import settings as settings_repo
from app.repositories import facts as fact_repo
from app.repositories import batches as batch_repo
from app.repositories import events as event_repo
from app.repositories import quarantine as quarantine_repo


def dashboard() -> dict[str, Any]:
    conn = connect(immediate=True)
    try:
        day = settings_repo.get(conn, "business_day")
        # Heal before counting so the overview never reports a dual home or a
        # torn batch as a stable state.
        from app.services import quarantine_bridge
        quarantine_bridge.reconcile_batches(conn)
        quarantine_bridge.reconcile_day(conn, day)
        facts = fact_repo.list_facts(conn, day)
        open_n = sum(1 for f in facts if f["status"] == "open")
        sealed_n = sum(1 for f in facts if f["status"] == "sealed")
        total = round(sum(float(f["amount"]) for f in facts), 2)
        events = event_repo.list_events(conn, day)
        batches = batch_repo.list_batches(conn)
        sealed_today = fact_repo.day_is_sealed(conn, day)
        q = quarantine_repo.counts_for_day(conn, day)

        # Every redeem event has exactly one home: a formal fact (legal, or a
        # promoted illegal) or a still-open quarantine row (quarantined /
        # discarded). A promoted row is the archived decision trail of its
        # fact, not a second home. So facts + non-promoted quarantine rows must
        # equal the redeem events, and a mismatch is exactly a dual home or a
        # homeless event.
        fact_events = {int(f["event_id"]) for f in facts}
        redeem_events = [e for e in events if e["event_type"] == "redeem"]
        legal_events = sum(1 for e in redeem_events if not (e.get("reason_code") or ""))
        illegal_events = len(redeem_events) - legal_events
        still_quarantined = {
            int(e["id"]) for e in redeem_events
            if e.get("quarantine_id") is not None
            and e.get("quarantine_status") in ("quarantined", "discarded")
        }
        archived_events = {
            int(e["id"]) for e in redeem_events if e.get("quarantine_id") is not None
        }
        dual_home_events = sum(
            1 for e in redeem_events
            if int(e["id"]) in fact_events and int(e["id"]) in still_quarantined
        )
        homeless_events = sum(
            1
            for e in redeem_events
            if int(e["id"]) not in fact_events and int(e["id"]) not in archived_events
        )
        formal_plus_quarantine = len(facts) + len(still_quarantined)
        return {
            "business_day": day,
            "event_count": len(events),
            "fact_count": len(facts),
            "open_fact_count": open_n,
            "sealed_fact_count": sealed_n,
            "redeem_total_today": total,
            "illegal_redeem_count": illegal_events,
            "legal_redeem_count": len(redeem_events) - illegal_events,
            "quarantine_count": q["quarantine_count"],
            "quarantine_unpromoted_count": len(still_quarantined),
            "quarantine_amount": q["quarantine_amount"],
            # A redeem event has exactly one home; both counters describe that
            # same partition and must always agree, including while a
            # promotion races a seal.
            "formal_plus_quarantine": formal_plus_quarantine,
            "event_illegal_plus_legal": illegal_events + legal_events,
            "dual_home_event_count": dual_home_events,
            "homeless_event_count": homeless_events,
            "quarantine_promoted_count": q["promoted_count"],
            "quarantine_discarded_count": q["discarded_count"],
            "batch_count": len(batches),
            "day_sealed": sealed_today,
        }
    finally:
        if conn.in_transaction:
            conn.commit()
        conn.close()
