"""Research module — the Research Lab's backend.

Runs LLM research as a background job (survives a refresh), ingests research papers
(paste or PDF upload with a page range), saves sessions, and pushes chosen ideas to a
saved prompt for later generation. Separate from generation by design.
"""

from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.jobs import jobs
from app.research import service

PREFIX = "/api/research"
router = APIRouter()


@router.get("/providers")
def providers():
    return service.providers()


class RunReq(BaseModel):
    category: str = ""
    region: str = "USA"
    delay: int = 1
    instrument: str = "EQUITY"
    dataset_names: list[str] = []
    fields: list[dict] = []
    categories: dict = {}                 # field_id -> category (for two-category mode)
    goal: str = ""
    paper_text: str = ""
    paper_name: str = ""
    paper_is_community: bool = False      # WorldQuant community paper → stricter grounding
    n: int = 6
    # single (default) | multi_single_dataset | multi_two_categories — multi only when chosen
    mode: str = "single"
    max_operators: int = 6


@router.post("/run")
def run(req: RunReq):
    if not req.fields:
        raise HTTPException(400, "Select some datafields first so the research is grounded in your data.")
    if req.paper_is_community and req.paper_text and not req.fields:
        raise HTTPException(400, "For a WorldQuant community paper, first select the datasets/fields it "
                                 "suggests so the research maps to the right data.")

    def task(progress, should_cancel):
        out = service.run_research(
            category=req.category, region=req.region, delay=req.delay, instrument=req.instrument,
            dataset_names=req.dataset_names, fields=req.fields, categories=req.categories, goal=req.goal,
            paper_text=req.paper_text, paper_is_community=req.paper_is_community,
            mode=req.mode, max_operators=req.max_operators, n=req.n, progress=progress)
        sid = service.save_session({**req.model_dump(), **out})
        out["session_id"] = sid
        return out

    return {"job_id": jobs.submit("research", task)}


class AutoReq(BaseModel):
    region: str = "IND"
    delay: int = 1
    instrument: str = "EQUITY"
    category: str = ""
    goal: str = ""
    paper_text: str = ""
    paper_name: str = ""
    n: int = 6
    max_operators: int = 6
    simulate: bool = True
    universes: list[str] = ["TOP3000"]
    neutralizations: list[str] = ["INDUSTRY"]
    decay: int = 4
    truncation: float = 0.08
    concurrency: int = 3
    limit_of_multi: int = 10
    min_sharpe: float | None = None
    min_fitness: float | None = None


@router.post("/autopilot")
def autopilot(req: AutoReq):
    """Run the full research → catalogue → expression → simulation pipeline unattended."""
    if req.n < 1 or req.n > 50:
        raise HTTPException(400, "Hypotheses must be between 1 and 50.")
    if not req.region.strip():
        raise HTTPException(400, "A region is required.")

    def task(progress, should_cancel):
        if should_cancel():
            return {"cancelled": True}
        result = service.run_autopilot(
            category=req.category, region=req.region.upper(), delay=req.delay,
            instrument=req.instrument, goal=req.goal, paper_text=req.paper_text,
            paper_name=req.paper_name, n=req.n, max_operators=req.max_operators,
            progress=progress)

        if should_cancel() or not req.simulate or not result.get("expressions"):
            return result

        progress(message=f"autopilot: sending {len(result['expressions'])} expressions to simulation…")
        from app.simulation import service as sim
        from app.simulation import service as simsvc
        t = sim.gate_thresholds(result["delay"])
        ms = req.min_sharpe if req.min_sharpe is not None else t["sharpe"]
        mf = req.min_fitness if req.min_fitness is not None else t["fitness"]
        sim_result = simsvc.run_simulation(
            expressions=result["expressions"], region=result["region"], delay=result["delay"],
            universes=req.universes or [result.get("universe", "TOP3000")],
            neutralizations=req.neutralizations or ["INDUSTRY"], decay=req.decay,
            truncation=req.truncation, test_period="P0Y", pasteurization="ON",
            unit_handling="VERIFY", nan_handling="OFF", max_trade="OFF",
            visualization=False, concurrency=req.concurrency, limit_of_multi=req.limit_of_multi,
            max_turnover=0.70, min_sharpe=ms, min_fitness=mf, max_corr=0.70,
            tag="AUTOPILOT", winner_tag="AUTOPILOT_WINNER", winner_color="GREEN",
            tag_winners_above=1.0, check_submission=False, get_pnl=False, get_stats=False,
            progress=progress, should_cancel=should_cancel)
        result["simulation"] = sim_result
        return result

    return {"job_id": jobs.submit("research-autopilot", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@router.post("/paper")
async def paper(file: UploadFile = File(...), pages: str = Form("")):
    """Extract text from an uploaded PDF (optionally a page range like '1-3,5')."""
    data = await file.read()
    try:
        text = service.extract_pdf_text(data, pages)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Could not read that PDF ({e}).")
    if not text:
        raise HTTPException(400, "No selectable text found in that PDF (is it a scan?).")
    return {"name": file.filename, "chars": len(text), "text": text[:80000]}


@router.get("/sessions")
def sessions(limit: int = 30):
    return {"sessions": service.list_sessions(limit)}


class PushReq(BaseModel):
    name: str = ""
    scope: str = "generate"      # generate | template
    category: str = ""
    region: str = ""
    body: str
    dataset_names: list[str] = []
    research_id: int = 0
    compose: bool = True         # AI-rewrite into a structured prompt with a nice name
    source: str = "research"     # research | strategy


@router.post("/push")
def push(req: PushReq):
    def task(progress, should_cancel):
        progress(message="naming and saving the prompt…")
        # Save the RAW notes as-is (no restructuring) with a nice name. The user can auto-rewrite
        # into a full master prompt on demand in Generation.
        name = (req.name or "").strip() or service.nice_name(req.body, req.category, req.region, req.source)
        saved = service.save_prompt(name=name, scope=req.scope, category=req.category,
                                    region=req.region, body=req.body, dataset_names=req.dataset_names,
                                    research_id=req.research_id)
        return {"ok": True, "prompt_id": saved["id"], "name": saved["name"], "duplicate": saved["duplicate"]}
    return {"job_id": jobs.submit("push-prompt", task)}
