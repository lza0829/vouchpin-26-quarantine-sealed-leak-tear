from __future__ import annotations
import sqlite3
from typing import Any

from app.repositories import campaigns as camp_repo
from app.repositories import events as event_repo
from app.repositories import facts as fact_repo

# Every reason an intake redeem can be refused a fact; the same codes are
# stored on the event and on the quarantine row, and returned in receipts.
REASON_LABELS: dict[str, str] = {
    "campaign_not_found": "活动不存在",
    "no_issue": "无对应发放",
    "non_positive_amount": "金额非正",
    "campaign_mismatch": "发放活动与核销活动不一致",
    "already_redeemed": "券码已核销",
    "day_sealed": "业务日已钉扎",
}


def validate_redeem(
    conn: sqlite3.Connection,
    *,
    coupon_code: str,
    campaign_code: str,
    amount: float,
    business_day: str,
    quarantine_id: int | None = None,
) -> tuple[str | None, str]:
    """Return (reason_code, reason); reason_code is None when the redeem is legal.

    A legal redeem must reference a known campaign, a positive amount, an
    existing issue event for the same coupon *and* campaign, must not already
    have a fact, and must land on a day that is not yet sealed.

    ``quarantine_id`` is set when re-validating a promotion: the fact it may
    already own (left behind by a half-finished promotion) is not a duplicate,
    but any other fact for the same coupon is.
    """
    campaign = camp_repo.get_by_code(conn, campaign_code)
    if campaign is None:
        return "campaign_not_found", f"活动 {campaign_code or '(空)'} 不存在"

    if amount <= 0:
        return "non_positive_amount", f"核销金额须为正数，收到 {amount}"

    issued_campaigns = event_repo.issue_campaigns(conn, coupon_code)
    if not issued_campaigns:
        return "no_issue", f"券码 {coupon_code} 无对应发放事件"
    if campaign_code not in issued_campaigns:
        return (
            "campaign_mismatch",
            f"券码 {coupon_code} 发放于 {', '.join(issued_campaigns)}，与核销活动 {campaign_code} 不一致",
        )

    existing = fact_repo.fact_for_coupon(conn, coupon_code)
    if existing is not None and quarantine_id is not None:
        owned = conn.execute(
            "SELECT fact_id FROM redeem_quarantine WHERE id=?",
            (int(quarantine_id),),
        ).fetchone()
        owned_fact = int(owned["fact_id"]) if owned and owned["fact_id"] is not None else None
        if owned_fact is None or int(existing["id"]) != owned_fact:
            return "already_redeemed", f"券码 {coupon_code} 已存在核销事实"
    elif existing is not None:
        return "already_redeemed", f"券码 {coupon_code} 已存在核销事实"

    if fact_repo.day_is_sealed(conn, business_day):
        return "day_sealed", f"业务日 {business_day} 核销已钉扎，不能再物化核销事实"

    return None, ""


def face_value(conn: sqlite3.Connection, campaign_code: str) -> float | None:
    campaign: dict[str, Any] | None = camp_repo.get_by_code(conn, campaign_code)
    return float(campaign["face_value"]) if campaign else None
