"""Success & Donation module — the device's tier plus the live success scorecard that the
donation ask is tied to. The studio invites support only after it has demonstrably helped."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.entitlement import service as ent
from app.analytics import service as analytics

PREFIX = "/api/tier"
router = APIRouter()


@router.get("/status")
def status():
    s = ent.status()
    summary = analytics.summary()
    s["success"] = {"total": summary["total"], "passed": summary["passed"],
                    "success_rate": summary["success_rate"]}
    # Donation CTA is hidden in the UI for now; the entitlement machinery below stays fully
    # functional so a supporter licence still verifies and unlocks.
    s["donation_visible"] = False
    return s


class ActivateReq(BaseModel):
    licence: dict


@router.post("/activate")
def activate(req: ActivateReq):
    """Redeem a signed licence for THIS device (the donation flow). Writes it locally and
    re-verifies offline; unlocks only if the signature matches this device."""
    return ent.activate(req.licence)
