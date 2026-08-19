"""Simulate module — run alphas across universes/neutralizations, judge them against the
success gate, tag with the user's own tags, and store results. The gate thresholds default
by delay (delay 0 is strict on Sharpe/Fitness) but the caller may override."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import jobs
from app.simulation import service

PREFIX = "/api/simulate"
router = APIRouter()


@router.get("/gate")
def gate(delay: int = 1):
    """The default gate thresholds for a delay — the UI shows these and lets the user edit."""
    t = service.gate_thresholds(delay)
    return {"delay": delay, "sharpe": t["sharpe"], "fitness": t["fitness"],
            "max_turnover": 0.70, "max_corr": 0.70}


class SimReq(BaseModel):
    expressions: list[str]
    region: str = "USA"
    delay: int = 1
    universes: list[str] = ["TOP3000"]
    neutralizations: list[str] = ["INDUSTRY"]
    decay: int = 4
    truncation: float = 0.08
    test_period: str = "P0Y"
    pasteurization: str = "ON"
    unit_handling: str = "VERIFY"
    nan_handling: str = "OFF"
    max_trade: str = "OFF"
    visualization: bool = False
    concurrency: int = 3
    limit_of_multi: int = 10
    max_turnover: float = 0.70
    max_corr: float = 0.70
    # thresholds — default by delay when not supplied
    min_sharpe: float | None = None
    min_fitness: float | None = None
    # user-chosen tags (nothing hardcoded)
    tag: str = ""
    winner_tag: str = ""
    winner_color: str = "GREEN"
    tag_winners_above: float = 1.0
    # optional deeper checks (correlations live in the submission check)
    check_submission: bool = False
    get_pnl: bool = False
    get_stats: bool = False
    execution_key: str = ""
    variant_id: int = 0
    experiment_id: int = 0   # set when these expressions came from a Research Engine experiment, for sim_results provenance


@router.post("/run")
def run(req: SimReq):
    if not req.expressions:
        raise HTTPException(400, "No expressions to simulate.")
    t = service.gate_thresholds(req.delay)
    min_sharpe = req.min_sharpe if req.min_sharpe is not None else t["sharpe"]
    min_fitness = req.min_fitness if req.min_fitness is not None else t["fitness"]

    def task(progress, should_cancel):
        return service.run_simulation(
            expressions=req.expressions, region=req.region, delay=req.delay,
            universes=req.universes or ["TOP3000"], neutralizations=req.neutralizations or ["INDUSTRY"],
            decay=req.decay, truncation=req.truncation, test_period=req.test_period,
            pasteurization=req.pasteurization, unit_handling=req.unit_handling, nan_handling=req.nan_handling,
            max_trade=req.max_trade, visualization=req.visualization, concurrency=req.concurrency,
            limit_of_multi=req.limit_of_multi, max_turnover=req.max_turnover, min_sharpe=min_sharpe,
            min_fitness=min_fitness, max_corr=req.max_corr, tag=req.tag, winner_tag=req.winner_tag,
            winner_color=req.winner_color, tag_winners_above=req.tag_winners_above,
            check_submission=req.check_submission, get_pnl=req.get_pnl, get_stats=req.get_stats,
            progress=progress, should_cancel=should_cancel, execution_key=req.execution_key, variant_id=req.variant_id,
            experiment_id=req.experiment_id)

    return {"job_id": jobs.submit("simulate", task)}


class SweepReq(BaseModel):
    expressions: list[str]
    regions: list[str]
    delay: int = 1
    instrument: str = "EQUITY"
    neutralizations: list[str] = ["INDUSTRY"]
    decay: int = 4
    truncation: float = 0.08
    concurrency: int = 3
    limit_of_multi: int = 10
    tag: str = ""
    winner_tag: str = ""
    home_region: str = ""   # where the alpha was built — ground truth for its datafields


@router.post("/sweep")
def sweep(req: SweepReq):
    """Cross-region auto-sweep: run the same expressions across several regions (per-region valid
    universe + gate)."""
    if not req.expressions:
        raise HTTPException(400, "No expressions to sweep.")
    if not req.regions:
        raise HTTPException(400, "Pick at least one region.")

    def task(progress, should_cancel):
        return service.run_cross_region_sweep(
            expressions=req.expressions, regions=req.regions, delay=req.delay, instrument=req.instrument,
            neutralizations=req.neutralizations, decay=req.decay, truncation=req.truncation,
            concurrency=req.concurrency, limit_of_multi=req.limit_of_multi, tag=req.tag,
            winner_tag=req.winner_tag, home_region=req.home_region,
            progress=progress, should_cancel=should_cancel)

    return {"job_id": jobs.submit("cross-region-sweep", task)}


class BatchReq(BaseModel):
    batches: list[dict]                 # [{label, expressions:[...]}]
    region: str = "USA"
    delay: int = 1
    universes: list[str] = ["TOP3000"]
    neutralizations: list[str] = ["INDUSTRY"]
    decay: int = 4
    truncation: float = 0.08
    concurrency: int = 3
    limit_of_multi: int = 10
    min_sharpe: float | None = None
    min_fitness: float | None = None
    tag: str = ""
    winner_tag: str = ""


@router.post("/batch")
def batch(req: BatchReq):
    """Queue several expression batches to run sequentially, unattended, in one background job."""
    if not req.batches:
        raise HTTPException(400, "The queue is empty.")
    t = service.gate_thresholds(req.delay)
    ms = req.min_sharpe if req.min_sharpe is not None else t["sharpe"]
    mf = req.min_fitness if req.min_fitness is not None else t["fitness"]

    def task(progress, should_cancel):
        return service.run_batch(
            batches=req.batches, region=req.region, delay=req.delay, universes=req.universes,
            neutralizations=req.neutralizations, decay=req.decay, truncation=req.truncation,
            concurrency=req.concurrency, limit_of_multi=req.limit_of_multi, min_sharpe=ms, min_fitness=mf,
            tag=req.tag, winner_tag=req.winner_tag, progress=progress, should_cancel=should_cancel)

    return {"job_id": jobs.submit("batch-queue", task)}


@router.post("/walkforward")
def walkforward(req: SweepReq):
    """Walk-forward robustness: run the expressions at both delay 1 and delay 0 in one region."""
    if not req.expressions:
        raise HTTPException(400, "No expressions.")
    region = req.regions[0] if req.regions else "USA"

    def task(progress, should_cancel):
        return service.run_walk_forward(
            expressions=req.expressions, region=region, instrument=req.instrument,
            neutralizations=req.neutralizations, decay=req.decay, truncation=req.truncation,
            concurrency=req.concurrency, limit_of_multi=req.limit_of_multi, tag=req.tag,
            winner_tag=req.winner_tag, progress=progress, should_cancel=should_cancel)

    return {"job_id": jobs.submit("walk-forward", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str):
    return {"cancelled": jobs.cancel(jid)}


@router.get("/results")
def results(limit: int = 200):
    return service.list_results(limit)
