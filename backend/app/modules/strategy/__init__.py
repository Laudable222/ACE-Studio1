"""Strategy Atlas module — per-category LLM strategy exploration (device-seeded, never
hardcoded), plus the category list learned by the knowledge DB."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import jobs
from app.strategy import service
from app.knowledge import service as knowledge

PREFIX = "/api/strategy"
router = APIRouter()


@router.get("/categories")
def categories():
    return {"categories": knowledge.overview().get("categories", [])}


class ExploreReq(BaseModel):
    category: str
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    n: int = 6
    mode: str = "single"   # single | two_categories


@router.post("/explore")
def explore(req: ExploreReq):
    def task(progress, should_cancel):
        return service.explore(req.category, req.region, req.delay, req.instrument, req.n, req.mode, progress)
    return {"job_id": jobs.submit("strategy", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j
