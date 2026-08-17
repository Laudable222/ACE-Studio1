from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.replication import service

PREFIX = "/api/replication"
router = APIRouter()

class PreviewReq(BaseModel):
    expression: str
    source_region: str = "IND"
    source_delay: int = 1
    source_universe: str = "TOP1000"
    target_region: str = "GBR"
    target_delay: int = 1
    target_universe: str = "TOP1000"
    mode: str = "concept"  # exact | equivalent | concept

@router.post("/preview")
def preview(req: PreviewReq):
    try:
        return service.preview(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Replication preview failed: {e}")
