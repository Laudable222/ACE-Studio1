"""SuperAlpha module — its own separated capability: vocabulary, selection template
expansion, static validation, the ≥10-component preflight count, LLM suggestion, and
simulation with the success gate. Selection/count/suggest and simulate run as jobs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import jobs
from app.super import service

PREFIX = "/api/super"
router = APIRouter()


@router.get("/vocab")
def vocab():
    return service.vocab()


class ExpandReq(BaseModel):
    templates: list[str] = []
    variables: dict[str, list] = {}
    paired: list[list[str]] = []
    max_expressions: int = 500


@router.post("/expand")
def expand(req: ExpandReq):
    return service.expand(req.templates, req.variables, req.paired, req.max_expressions)


class ValidateReq(BaseModel):
    selections: list[str] = []
    combos: list[str] = []


@router.post("/validate")
def validate(req: ValidateReq):
    return service.validate(req.selections, req.combos)


class PreviewReq(BaseModel):
    selections: list[str]
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    selection_limit: int = 1000
    selection_handling: str = "POSITIVE"
    min_count: int = service.SUPER_MIN_COMPONENTS


@router.post("/selection/preview")
def preview(req: PreviewReq):
    def task(progress, should_cancel):
        return service.selection_preview(req.selections, req.region, req.delay, req.instrument,
                                         req.selection_limit, req.selection_handling, req.min_count,
                                         progress, should_cancel)
    return {"job_id": jobs.submit("selection", task)}


class SuggestReq(BaseModel):
    kind: str = "selection"
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universe: str = "TOP3000"
    n: int = 8
    own: bool = True


@router.post("/suggest")
def suggest(req: SuggestReq):
    if req.kind not in ("selection", "combo"):
        raise HTTPException(400, "kind must be 'selection' or 'combo'")

    def task(progress, should_cancel):
        return service.suggest(req.kind, req.region, req.delay, req.instrument, req.universe,
                               req.n, req.own, progress)
    return {"job_id": jobs.submit("super_suggest", task)}


class SimReq(BaseModel):
    selections: list[str]
    combos: list[str] = ["1"]
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universes: list[str] = ["ILLIQUID_MINVOL1M"]
    neutralizations: list[str] = ["FAST"]
    decay: int = 10
    truncation: float = 0.08
    test_period: str = "P0Y"
    pasteurization: str = "ON"
    unit_handling: str = "VERIFY"
    nan_handling: str = "OFF"
    max_trade: str = "OFF"
    selection_limit: int = 1000
    selection_handling: str = "POSITIVE"
    concurrency: int = 3
    max_turnover: float = 0.70
    max_corr: float = 0.70
    min_sharpe: float | None = None
    min_fitness: float | None = None
    tag: str = ""
    winner_tag: str = ""
    winner_color: str = "GREEN"
    tag_winners_above: float = 1.0
    check_submission: bool = False


@router.post("/simulate")
def simulate(req: SimReq):
    from app.simulation.service import gate_thresholds
    t = gate_thresholds(req.delay)
    ms = req.min_sharpe if req.min_sharpe is not None else t["sharpe"]
    mf = req.min_fitness if req.min_fitness is not None else t["fitness"]

    def task(progress, should_cancel):
        return service.run_simulation(
            selections=req.selections, combos=req.combos or ["1"], region=req.region, delay=req.delay,
            instrument=req.instrument, universes=req.universes, neutralizations=req.neutralizations,
            decay=req.decay, truncation=req.truncation, test_period=req.test_period,
            pasteurization=req.pasteurization, unit_handling=req.unit_handling, nan_handling=req.nan_handling,
            max_trade=req.max_trade, selection_limit=req.selection_limit, selection_handling=req.selection_handling,
            concurrency=req.concurrency, max_turnover=req.max_turnover, min_sharpe=ms, min_fitness=mf,
            max_corr=req.max_corr, tag=req.tag, winner_tag=req.winner_tag, winner_color=req.winner_color,
            tag_winners_above=req.tag_winners_above, check_submission=req.check_submission,
            progress=progress, should_cancel=should_cancel)
    return {"job_id": jobs.submit("super", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@router.post("/jobs/{jid}/cancel")
def cancel(jid: str):
    return {"cancelled": jobs.cancel(jid)}
