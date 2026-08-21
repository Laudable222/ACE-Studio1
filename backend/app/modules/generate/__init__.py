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


class BulkFieldGenReq(BaseModel):
    text: str = ""                        # pasted datafield descriptions, one per line
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    universe: str = "TOP3000"
    max_operators: int = 4
    n: int = 12
    rounds: int = 4                       # per mode — see /run's experiment-generation sibling for the same pattern


@router.post("/bulk")
def bulk(req: BulkFieldGenReq):
    if not req.text.strip():
        raise HTTPException(400, "Paste the datafield descriptions to build alphas from — one per line.")

    def task(progress, should_cancel):
        from app.knowledge import service as knowledge_service
        from llm_providers import _canonical

        progress(message="matching pasted lines against the local catalogue…")
        fields, categories, dataset_names, unmatched = knowledge_service.resolve_fields_from_text(
            req.text, region=req.region, delay=req.delay, instrument=req.instrument, universe=req.universe)
        if not fields:
            raise RuntimeError(
                f"None of the pasted lines matched fields catalogued for {req.region} D{req.delay} {req.universe}. "
                "Fetch datasets for this region/delay in Data Explorer first (it catalogues fields too), or check "
                "the pasted text — a bare field id per line matches most reliably.")
        progress(message=f"matched {len(fields)} field(s) across {len(dataset_names)} dataset(s)"
                        + (f" — {len(unmatched)} line(s) didn't match anything" if unmatched else ""))

        # Individually AND in combination: one pass in 'single' mode (deep per-field extraction,
        # naturally spread across every matched field per the generation prompt's own coverage
        # rule) and one pass in 'multi' mode (≤2-category combinations, same rule enforced).
        # Each pass runs the same multi-round accumulation as experiment generation — keep only
        # genuinely new distinct expressions per round, stop the moment a round adds nothing new.
        by_mode: dict[str, list[str]] = {"single": [], "multi": []}
        seen = set()
        for mode in ("single", "multi"):
            if should_cancel(): break
            rounds = max(1, min(6, req.rounds))
            for i in range(rounds):
                if should_cancel(): break
                progress(message=f"{mode}-field generation — round {i + 1}/{rounds}…")
                out = service.run_generation(
                    mode=mode, prompt="", region=req.region, delay=req.delay, instrument=req.instrument,
                    universe=req.universe, dataset_names=dataset_names, fields=fields, categories=categories,
                    max_operators=req.max_operators, n=req.n, repair_rounds=2, region_note="",
                    progress=progress, raw_prompt=False)
                round_new = [e for e in out.get("valid", []) if _canonical(e) not in seen]
                for e in round_new: seen.add(_canonical(e))
                by_mode[mode] += round_new
                if i > 0 and not round_new: break

        valid = by_mode["single"] + by_mode["multi"]
        from app.discovery import service as dsvc
        for e in valid:
            try: dsvc.save_dna(dsvc.alpha_dna(e, req.region, categories))
            except Exception: pass
        progress(message=f"done: {len(by_mode['single'])} single-field + {len(by_mode['multi'])} combination candidate(s)")
        return {"valid": valid, "single": by_mode["single"], "multi": by_mode["multi"],
                "matched_field_ids": [f["id"] for f in fields], "dataset_names": dataset_names, "unmatched": unmatched}

    return {"job_id": jobs.submit("bulk-field-generation", task)}


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
