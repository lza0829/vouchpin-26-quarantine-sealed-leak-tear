from __future__ import annotations

import sqlite3
from typing import Any

from app.repositories import audit as audit_repo
from app.repositories import batches as batch_repo
from app.repositories import facts as fact_repo
from app.repositories import quarantine as quarantine_repo


def project(conn: sqlite3.Connection, business_day: str) -> list[dict[str, Any]]:
    """Rows that are *still* quarantined and therefore never pinnable."""
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


def excluded_summary(conn: sqlite3.Connection, business_day: str) -> dict[str, Any]:
    rows = project(conn, business_day)
    return {
        "count": len(rows),
        "amount": round(sum(float(r["amount"]) for r in rows), 2),
    }


def pinnable(conn: sqlite3.Connection, business_day: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a day into (pinnable open facts, still-quarantined rows).

    The quarantine table is authoritative at preview/seal time. A row that is
    still quarantined owns its event: no formal fact for that event may enter
    the preview or the batch, even if a half-finished promotion left one
    behind. A legal fact for a different event that happens to share a coupon
    code is unrelated and stays pinnable.
    """
    blocked = [r for r in quarantine_repo.list_rows(conn, business_day) if str(r["status"]) != "promoted"]
    blocked_events = {int(r["event_id"]) for r in blocked}
    opens = [
        f
        for f in fact_repo.open_facts_for_day(conn, business_day)
        if int(f["event_id"]) not in blocked_events
    ]
    return opens, project(conn, business_day)


def reconcile_day(conn: sqlite3.Connection, business_day: str, actor: str = "") -> dict[str, Any]:
    """Make quarantine and formal facts agree before any read or seal.

    Both arms of the boundary are enforced, keyed on the event (the shared
    identity of an event, its quarantine row and its fact):

    * Formal wins once the event is already sealed — clear the isolation, link
      the fact and write the promotion audit so the two homes cannot coexist.
    * Quarantine wins for a half promotion — an open fact whose event is still
      quarantined/discarded is a torn write; it is rolled back so the event
      stays claimable only through a fresh promotion.
    * A ``promoted`` receipt with no fact is a lie; revert it to quarantined.

    The caller owns the transaction (always invoked under ``BEGIN IMMEDIATE``).
    """
    promoted_reconciled = 0
    rolled_back = 0
    for row in quarantine_repo.list_rows(conn, business_day):
        qid = int(row["id"])
        event_id = int(row["event_id"])
        coupon = str(row["coupon_code"])
        status = str(row["status"])
        fact = fact_repo.fact_for_event(conn, event_id)

        if status == "promoted":
            if fact is not None:
                if int(row.get("fact_id") or 0) != int(fact["id"]):
                    quarantine_repo.link_promoted_fact(conn, qid, int(fact["id"]))
                continue
            # Receipt claims success but there is no formal fact: fake promotion.
            quarantine_repo.revert_to_quarantined(conn, qid)
            audit_repo.log(
                conn,
                action="promotion_rollback",
                target_type="quarantine",
                target_id=qid,
                detail={
                    "event_id": event_id,
                    "business_day": business_day,
                    "coupon_code": coupon,
                    "note": "晋升回执无对应正式事实，恢复为隔离",
                },
                actor=actor,
            )
            rolled_back += 1
            continue

        # status is quarantined or discarded: the row still owns its event.
        if fact is None:
            continue

        if str(fact["status"]) == "sealed":
            # The event is already pinned. Formal is the fact of record, so
            # the isolation must be cleared instead of leaving a dual home.
            if status == "quarantined":
                quarantine_repo.mark_promoted(conn, qid, int(fact["id"]), row.get("decision_note") or "")
            else:
                quarantine_repo.force_promoted(conn, qid, int(fact["id"]))
            audit_repo.log(
                conn,
                action="promote_reconciled",
                target_type="quarantine",
                target_id=qid,
                detail={
                    "event_id": event_id,
                    "fact_id": int(fact["id"]),
                    "fact_status": "sealed",
                    "business_day": business_day,
                    "coupon_code": coupon,
                    "note": "正式事实已钉扎，强制清隔离并补晋升审计",
                },
                actor=actor,
            )
            promoted_reconciled += 1
            continue

        # Open fact on an event that is still quarantined/discarded: half
        # promotion, never allowed to reach a batch.
        fact_repo.delete_fact(conn, int(fact["id"]))
        audit_repo.log(
            conn,
            action="promotion_rollback",
            target_type="quarantine",
            target_id=qid,
            detail={
                "event_id": event_id,
                "fact_id": int(fact["id"]),
                "business_day": business_day,
                "coupon_code": coupon,
                "note": "隔离未清却存在 open 正式行，回滚半截行",
            },
            actor=actor,
        )
        rolled_back += 1
    return {"promoted_reconciled": promoted_reconciled, "promotion_rolled_back": rolled_back}



def reconcile_batches(conn: sqlite3.Connection, actor: str = "") -> dict[str, Any]:
    """Keep batch headers equal to the rows they own, and abort half batches.

    A batch that never reached ``sealed`` (a torn seal interrupted after the
    header/rows were written) releases its rows back to ``open`` and is marked
    ``aborted``; it must never masquerade as the current sealed batch.
    """
    aborted = 0
    healed = 0
    for batch in batch_repo.list_batches(conn):
        bid = int(batch["id"])
        rows = fact_repo.facts_for_batch(conn, bid)
        if str(batch["status"]) != "sealed":
            fact_repo.reset_batch(conn, bid)
            batch_repo.set_status(conn, bid, "aborted")
            batch_repo.update_header(conn, bid, fact_count=0, total_amount=0.0)
            audit_repo.log(
                conn,
                action="batch_aborted",
                target_type="batch",
                target_id=bid,
                detail={
                    "business_day": batch["business_day"],
                    "released_fact_count": len(rows),
                    "note": "半截批次未完成，释放明细并标记 aborted",
                },
                actor=actor,
            )
            aborted += 1
            continue
        fact_count = len(rows)
        total = round(sum(float(r["amount"]) for r in rows), 2)
        if fact_count != int(batch["fact_count"]) or total != round(
            float(batch["total_amount"]), 2
        ):
            batch_repo.update_header(conn, bid, fact_count=fact_count, total_amount=total)
            audit_repo.log(
                conn,
                action="batch_header_reconciled",
                target_type="batch",
                target_id=bid,
                detail={
                    "business_day": batch["business_day"],
                    "fact_count": fact_count,
                    "total_amount": total,
                    "note": "批头按批内明细回写",
                },
                actor=actor,
            )
            healed += 1
    return {"batches_aborted": aborted, "batches_healed": healed}


def promote_to_fact(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    *,
    amount: float,
    campaign_code: str,
    actor: str = "",
) -> int:
    """Materialise a quarantine row into an open fact, atomically.

    The fact insert, the event-chain cleanup and the quarantine state change
    happen in the caller's transaction. Any failure rolls the whole promotion
    back, so a ``success`` receipt can never leave the row still quarantined.
    """
    qid = int(row["id"])
    event_id = int(row["event_id"])
    existing = fact_repo.fact_for_event(conn, event_id)
    if existing is not None:
        # Formal-fact arm: never create a second fact for the same event.
        fact_id = int(existing["id"])
    else:
        fact_id = fact_repo.insert_fact(
            conn,
            coupon_code=str(row["coupon_code"]),
            campaign_code=campaign_code,
            amount=float(amount),
            business_day=str(row["business_day"]),
            event_id=event_id,
            status="open",
        )
    changed = quarantine_repo.mark_promoted(conn, qid, fact_id, "")
    if changed != 1:
        raise RuntimeError(f"隔离行 #{qid} 晋升时状态已变化，事务回滚")
    fact = fact_repo.get_fact(conn, fact_id)
    if fact is None or int(fact["event_id"]) != event_id:
        raise RuntimeError(f"隔离行 #{qid} 晋升后未找到对应正式事实，事务回滚")
    return fact_id
