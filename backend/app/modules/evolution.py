from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app import evolution_service as svc
from app.core.jobs import jobs
from app.simulation import service as sim_service

PREFIX = "/api/evolution"
router = APIRouter()

class FamilyReq(BaseModel):
    alpha_id: str
    name: str = ""
    hypothesis: dict = {}
    budget: int = 30

class EvolveReq(BaseModel):
    family_id: int
    max_variants: int = 10

class CloseReq(BaseModel):
    family_id: int
    reason: str = ""

class AbandonReq(BaseModel):
    variant_id: int
    reason: str = ""

@router.get("/families")
def families(limit: int = 50, status: str = ""):
    return {"families": svc.list_families(limit, status)}

@router.get("/diagnose/{alpha_id}")
def diagnose(alpha_id: str):
    try: return svc.diagnose(alpha_id)
    except ValueError as e: raise HTTPException(404, str(e))

@router.post("/families")
def create(req: FamilyReq):
    try: return svc.create_family(req.alpha_id, req.name, req.hypothesis, req.budget)
    except ValueError as e: raise HTTPException(400, str(e))

@router.post("/evolve")
def evolve(req: EvolveReq):
    try: return svc.evolve(req.family_id, req.max_variants)
    except ValueError as e: raise HTTPException(400, str(e))

@router.post("/close")
def close(req: CloseReq):
    try: return svc.close_family(req.family_id, req.reason)
    except ValueError as e: raise HTTPException(400, str(e))

@router.post("/abandon")
def abandon(req: AbandonReq):
    try: return svc.abandon_variant(req.variant_id, req.reason)
    except ValueError as e: raise HTTPException(400, str(e))

@router.post("/attach/{alpha_id}")
def attach(alpha_id: str):
    try: return svc.mark_variant_from_sim(alpha_id)
    except ValueError as e: raise HTTPException(404, str(e))


class SimVariantReq(BaseModel):
    variant_id:int
    # Optional execution overrides. If omitted, the variant's inherited settings are used exactly.
    universes:list[str]=[]
    neutralizations:list[str]=[]
    decay:int|None=None
    truncation:float|None=None
    test_period:str|None=None
    pasteurization:str|None=None
    unit_handling:str|None=None
    nan_handling:str|None=None
    max_trade:str|None=None

class RepairReq(BaseModel):
    variant_id:int
    max_tokens:int=4000

@router.post("/repair")
def repair(req:RepairReq):
    try:
        return svc.repair_variant(req.variant_id, req.max_tokens)
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.post("/simulate-variant")
def simulate_variant(req:SimVariantReq):
    from app.db.base import SessionLocal
    from app.db import models as M
    import json, copy
    with SessionLocal() as db:
        v=db.get(M.AlphaVariant,req.variant_id)
        f=db.get(M.AlphaFamily,v.family_id) if v else None
        if not v: raise HTTPException(404,"alpha variant not found")
        if v.status not in ("proposed","failed") or v.mutation_type == "repair": raise HTTPException(400,"only proposed/failed variants can be simulated")
        if not v.execution_key: v.execution_key=__import__("uuid").uuid4().hex
        settings=json.loads(v.settings_json or "{}")
        settings.update({k:val for k,val in {"universe": (req.universes[0] if req.universes else None), "neutralization": (req.neutralizations[0] if req.neutralizations else None), "decay":req.decay, "truncation":req.truncation, "test_period":req.test_period, "pasteurization":req.pasteurization, "unit_handling":req.unit_handling, "nan_handling":req.nan_handling, "max_trade":req.max_trade}.items() if val is not None})
        region=f.region or settings.get("region") or "IND"
        delay=int(settings.get("delay",1))
        universe=settings.get("universe") or "TOP1000"
        neutral=settings.get("neutralization") or "INDUSTRY"
        decay=int(settings.get("decay",4)); trunc=float(settings.get("truncation",.08))
        test_period=settings.get("test_period","P0Y"); pasteurization=settings.get("pasteurization","ON")
        unit_handling=settings.get("unit_handling","VERIFY"); nan_handling=settings.get("nan_handling","OFF"); max_trade=settings.get("max_trade","OFF")
        key=v.execution_key; vid=v.id; expr=v.expression
        v.settings_json=json.dumps(settings); v.status="running"; db.commit()
    def task(progress,should_cancel):
        try:
            result=sim_service.run_simulation(expressions=[expr],region=region,delay=delay,universes=[universe],neutralizations=[neutral],decay=decay,truncation=trunc,test_period=test_period,pasteurization=pasteurization,unit_handling=unit_handling,nan_handling=nan_handling,max_trade=max_trade,visualization=False,concurrency=1,limit_of_multi=10,max_turnover=.70,min_sharpe=None,min_fitness=None,max_corr=.70,tag="",winner_tag="",winner_color="GREEN",tag_winners_above=1.0,check_submission=False,get_pnl=False,get_stats=False,progress=progress,should_cancel=should_cancel,execution_key=key,variant_id=vid)
            attached=svc.attach_simulation_to_variant(vid,key)
            return {"simulation":result,"variant":attached}
        except Exception as exc:
            with SessionLocal() as db:
                vv=db.get(M.AlphaVariant,vid)
                if vv:
                    vv.status="failed"; vv.closed_reason=f"simulation error: {str(exc)[:500]}"; db.commit()
            raise
    return {"job_id":jobs.submit("evolution-simulation",task),"variant_id":vid,"execution_key":key}
