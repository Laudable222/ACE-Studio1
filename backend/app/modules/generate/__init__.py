"""Generation module — deep single-field / ≤2-category multi-field LLM generation, static
validation, the operator list (for autocomplete + checks), and the Prompt Library."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import jobs
from app.generation import service

PREFIX = "/api/generate"
router = APIRouter()


class GenReq(BaseModel):
    mode: str = "single"                 # single | multi
    prompt: str = ""
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universe: str = "TOP3000"
    dataset_names: list[str] = []
    fields: list[dict] = []
    categories: dict[str, str] = {}      # field_id -> category
    max_operators: int = 4
    n: int = 12
    repair_rounds: int = 2
    region_note: str = ""
    raw_prompt: bool = False              # send `prompt` to the LLM as-is (a master prompt)


@router.post("/run")
def run(req: GenReq):
    if not req.fields and not req.prompt.strip():
        raise HTTPException(400, "Select datafields, or describe the idea you want tested so the LLM can pick the data itself.")

    def task(progress, should_cancel):
        fields, categories, dataset_names = req.fields, req.categories, req.dataset_names
        if not fields:
            # No manual selection — score the local catalogue against the instruction itself and
            # pick the fields, the same way Autopilot grounds each hypothesis. Never queries BRAIN.
            from app.knowledge import service as knowledge_service
            progress(message="no fields selected — scoring the local catalogue against your instruction…")
            fields, categories, dataset_names = knowledge_service.auto_select_fields(
                query=req.prompt, region=req.region, delay=req.delay, instrument=req.instrument, universe=req.universe)
            if not fields:
                raise RuntimeError(
                    f"No catalogued fields for {req.region} D{req.delay} {req.universe} matched your instruction. "
                    "Fetch datasets for this region/delay in Data Explorer (it catalogues their fields too), "
                    "or select fields manually.")
            progress(message=f"auto-selected {len(fields)} field(s) across {len(dataset_names)} dataset(s) — generating…")
        return service.run_generation(
            mode=req.mode, prompt=req.prompt, region=req.region, delay=req.delay,
            instrument=req.instrument, universe=req.universe, dataset_names=dataset_names,
            fields=fields, categories=categories, max_operators=req.max_operators,
            n=req.n, repair_rounds=req.repair_rounds, region_note=req.region_note,
            raw_prompt=req.raw_prompt, progress=progress)

    return {"job_id": jobs.submit("generate", task)}


@router.post("/rewrite")
def rewrite(req: GenReq):
    """Auto-rewrite the current instruction into a long, self-contained MASTER prompt that
    re-provides the datasets, operators (with examples), datafields and all hypotheses/strategy."""
    def task(progress, should_cancel):
        progress(message="composing a master prompt…")
        master = service.rewrite_master_prompt(
            raw=req.prompt, region=req.region, delay=req.delay, instrument=req.instrument,
            universe=req.universe, dataset_names=req.dataset_names, fields=req.fields,
            categories=req.categories, max_operators=req.max_operators, n=req.n)
        return {"prompt": master}
    return {"job_id": jobs.submit("rewrite", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


class TemplatizeReq(BaseModel):
    expression: str


@router.post("/templatize")
def templatize(req: TemplatizeReq):
    """Turn a concrete expression into a {field}/{field2} template + its datasets, for reuse."""
    return service.templatize(req.expression)


class ValidateReq(BaseModel):
    expressions: list[str]
    fields: list[dict] = []
    max_operators: int = 4
    multi_field: bool = False


@router.post("/validate")
def validate(req: ValidateReq):
    return service.validate_expressions(req.expressions, req.fields, req.max_operators, req.multi_field)


class TemplateExpandReq(BaseModel):
    templates: list[str]
    field_ids: list[str]
    field2_ids: list[str] = []          # pool for {field2}; empty = same as field_ids
    fields: list[dict] = []
    vec_ops: list[str] = ["vec_avg", "vec_max", "vec_min", "vec_norm"]
    max_operators: int = 4
    multi_field: bool = False
    max_combos: int = 60


@router.post("/templates/expand")
def templates_expand(req: TemplateExpandReq):
    return service.expand_templates(req.templates, req.field_ids, req.fields, req.vec_ops,
                                    req.max_operators, req.multi_field, req.max_combos,
                                    field2_ids=req.field2_ids)


class TemplateSuggestReq(BaseModel):
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universe: str = "TOP3000"
    dataset_names: list[str] = []
    fields: list[dict] = []
    categories: dict[str, str] = {}
    max_operators: int = 4
    n: int = 6
    multi_field: bool = False
    idea: str = ""   # used to auto-select fields from the catalogue when none are given below


@router.post("/templates/suggest")
def templates_suggest(req: TemplateSuggestReq):
    if not req.fields and not req.idea.strip():
        raise HTTPException(400, "Select datafields, or describe the idea so the LLM can pick the data itself.")

    def task(progress, should_cancel):
        fields, categories, dataset_names = req.fields, req.categories, req.dataset_names
        if not fields:
            from app.knowledge import service as knowledge_service
            progress(message="no fields selected — scoring the local catalogue against your idea…")
            fields, categories, dataset_names = knowledge_service.auto_select_fields(
                query=req.idea, region=req.region, delay=req.delay, instrument=req.instrument, universe=req.universe)
            if not fields:
                raise RuntimeError(
                    f"No catalogued fields for {req.region} D{req.delay} {req.universe} matched your idea. "
                    "Fetch datasets for this region/delay in Data Explorer, or select fields manually.")
            progress(message=f"auto-selected {len(fields)} field(s) across {len(dataset_names)} dataset(s)")
        return service.suggest_templates(region=req.region, delay=req.delay, instrument=req.instrument,
                                         universe=req.universe, dataset_names=dataset_names,
                                         fields=fields, categories=categories,
                                         max_operators=req.max_operators, n=req.n,
                                         multi_field=req.multi_field, progress=progress)
    return {"job_id": jobs.submit("templates", task)}


@router.get("/operators")
def operators():
    try:
        ops = service.operators_list()
    except Exception as e:  # noqa: BLE001 — clean message if no session
        raise HTTPException(409, str(e))
    return {"count": len(ops), "operators": ops}


# ── prompt library ───────────────────────────────────────────────────────────────────

@router.get("/prompts")
def prompts(scope: str = ""):
    return {"prompts": service.list_prompts(scope)}


@router.post("/prompts/{pid}/delete")
def delete_prompt(pid: int):
    service.delete_prompt(pid)
    return {"ok": True}


@router.get("/export")
def export_all():
    """Portable bundle of saved prompts + research sessions (for backup / sharing setups)."""
    from app.research import service as rs
    return rs.export_all()


class ImportReq(BaseModel):
    format: str = ""
    prompts: list[dict] = []
    research_sessions: list[dict] = []


@router.post("/import")
def import_all(req: ImportReq):
    from app.research import service as rs
    return rs.import_all(req.model_dump())
