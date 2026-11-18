from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Query
from app.services import redeem_service as svc

router = APIRouter(tags=["redeem"])


@router.get("/facts")
def facts(business_day: str | None = Query(None)):
    return svc.list_facts(business_day)


@router.get("/redeem/preview")
def preview(business_day: str | None = Query(None)):
    return svc.preview(business_day)


@router.post("/redeem/seal")
def seal(body: dict[str, Any]):
    return svc.seal(body)


@router.get("/batches")
def batches():
    return svc.list_batches()


@router.get("/quarantine")
def quarantine(
    business_day: str | None = Query(None),
    status: str | None = Query(None),
):
    return svc.list_quarantine(business_day, status)


@router.post("/quarantine/{quarantine_id}/promote")
def promote(quarantine_id: int, body: dict[str, Any]):
    return svc.promote(quarantine_id, body)


@router.post("/quarantine/{quarantine_id}/discard")
def discard(quarantine_id: int, body: dict[str, Any]):
    return svc.discard(quarantine_id, body)


@router.get("/audit")
def audit(
    target_type: str | None = Query(None),
    target_id: int | None = Query(None),
):
    return svc.list_audit(target_type, target_id)
