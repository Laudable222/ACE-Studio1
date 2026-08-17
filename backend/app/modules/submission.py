from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import submission_service as svc

PREFIX = "/api/submission"
router = APIRouter()


class SettingsReq(BaseModel):
    daily_limit: int = 4
    timezone: str = "Africa/Lagos"


class QueueReq(BaseModel):
    alpha_ids: list[str] = []
    notes: str = ""


class RecordReq(BaseModel):
    record_id: int

class QueueSimulationReq(BaseModel):
    sim_result_ids: list[int] = []
    notes: str = ""


@router.get("/status")
def status():
    return svc.status()


@router.get("/queue")
def queue(limit: int = 200):
    return {"records": svc.list_queue(limit)}


@router.get("/candidates")
def candidates(limit: int = 200):
    rows = svc.candidates(limit)
    for r in rows:
        r["score"] = svc.rank_score(r)
    rows.sort(key=lambda x: (-x["score"], -(abs(x.get("fitness") or 0))))
    return {"candidates": rows}


@router.post("/settings")
def settings(req: SettingsReq):
    try:
        return svc.set_settings(req.daily_limit, req.timezone)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/queue")
def add_to_queue(req: QueueReq):
    ids = list(dict.fromkeys(x.strip() for x in req.alpha_ids if x.strip()))
    if not ids:
        raise HTTPException(400, "Select at least one alpha.")
    added, errors = [], []
    for aid in ids:
        try:
            added.append(svc.queue_alpha(aid, req.notes))
        except Exception as e:
            errors.append({"alpha_id": aid, "error": str(e)})
    return {"added": added, "errors": errors}


@router.post("/queue-simulations")
def queue_simulations(req: QueueSimulationReq):
    ids=list(dict.fromkeys(int(x) for x in req.sim_result_ids))
    if not ids: raise HTTPException(400,"Select at least one simulation result.")
    added=[]; errors=[]
    for sid in ids:
        try: added.append(svc.queue_simulation(sid, req.notes))
        except Exception as e: errors.append({"sim_result_id":sid,"error":str(e)})
    return {"added":added,"errors":errors}

@router.delete("/queue/{record_id}")
def remove(record_id: int):
    try:
        return svc.remove_alpha(record_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/submit")
def submit(req: RecordReq):
    try:
        return svc.submit_record(req.record_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Submission failed: {str(e).splitlines()[0][:400]}")


@router.post("/retry")
def retry(req: RecordReq):
    try:
        return svc.reset_error(req.record_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
