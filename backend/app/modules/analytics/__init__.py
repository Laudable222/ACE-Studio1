"""Analytics module — success-rate summary + operator insights, and pairwise PnL
correlation with the largest mutually-uncorrelated set. No submission happens here; the
studio only tells the user which alphas are safe to submit together."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import jobs
from app.analytics import service
from app.simulation import service as sim

PREFIX = "/api/analytics"
router = APIRouter()


@router.get("/summary")
def summary():
    return service.summary()


@router.get("/results")
def results(limit: int = 300):
    # richer result list lives in the simulation service (stored SimResults)
    return sim.list_results(limit)


@router.get("/passed")
def passed(limit: int = 40):
    return {"alpha_ids": service.passed_alpha_ids(limit)}


@router.get("/ledger")
def ledger():
    """Experiment ledger — multiple-testing counts per region/dataset."""
    return service.experiment_ledger()


@router.get("/alpha/{alpha_id}/pnl")
def alpha_pnl(alpha_id: str):
    """Cumulative PnL series + yearly stats for an alpha — powers the Results equity curve."""
    return {"pnl": service.engine.alpha_pnl(alpha_id), "yearly": service.engine.alpha_yearly(alpha_id)}


class CorrReq(BaseModel):
    alpha_ids: list[str]
    threshold: float = 0.7
    years: int = 4


class ProdCorrReq(BaseModel):
    alpha_ids: list[str]
    threshold: float = 0.7


@router.post("/prodcorr")
def prodcorr(req: ProdCorrReq):
    """Production-correlation check (the real submission gate) — per alpha, not as a group."""
    def task(progress, should_cancel):
        try:
            return service.prod_corr_check(req.alpha_ids, req.threshold, progress=progress)
        except service.engine.SessionExpired as e:
            raise RuntimeError(str(e))

    return {"job_id": jobs.submit("prod-corr", task)}


@router.post("/correlation")
def correlation(req: CorrReq):
    def task(progress, should_cancel):
        try:
            return service.correlation(req.alpha_ids, req.threshold, req.years, progress=progress)
        except service.engine.SessionExpired as e:
            raise RuntimeError(str(e))

    return {"job_id": jobs.submit("correlation", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j
