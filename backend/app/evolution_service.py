"""Alpha Evolution Engine.

Failed alphas are research nodes, not trash. This service creates controlled, traceable
variants from a failed parent, changing one research dimension at a time whenever possible.
It never auto-submits and never silently launches simulations. Proposed variants are persisted
so the user/LLM/simulation pipeline can pick them up deliberately.
"""
from __future__ import annotations

import json, re, time, uuid
from dataclasses import dataclass
from sqlalchemy import select, func
from app.db.base import SessionLocal
from app.db import models as M

MAX_VARIANTS_PER_FAMILY = 30
MAX_GENERATIONS = 3


def _now(): return time.time()

def _json(s, fallback):
    try: return json.loads(s or "")
    except Exception: return fallback


def _sim(db, alpha_id: str):
    return db.scalar(select(M.SimResult).where(M.SimResult.alpha_id == str(alpha_id)).order_by(M.SimResult.created_at.desc()))

def _sim_for_variant(db, variant_id: int, execution_key: str = ""):
    q = select(M.SimResult).where(M.SimResult.variant_id == int(variant_id))
    if execution_key:
        q = q.where(M.SimResult.execution_key == execution_key)
    return db.scalar(q.order_by(M.SimResult.created_at.desc()))


def _dna(db, expression: str):
    from app.discovery.service import canonical
    return db.scalar(select(M.AlphaDNA).where(M.AlphaDNA.expression_key == canonical(expression)))


def diagnose_sim_result(s) -> dict:
    """Diagnose exactly this stored simulation result. No alpha-id lookup is performed here, so
    Evolution cannot accidentally diagnose a sibling/parent simulation."""
    reasons = _json(s.gate_reasons, [])
    cfg = _json(s.execution_config_json, {}) or {}
    thr = cfg.get("gate_thresholds") or {}
    sharpe_thr = float(thr.get("sharpe", 2.69 if int(s.delay) == 0 else 1.58))
    fitness_thr = float(thr.get("fitness", 1.5 if int(s.delay) == 0 else 1.0))
    max_turn = float(thr.get("max_turnover", 0.70))
    max_corr = float(thr.get("max_corr", 0.70))
    failures = []
    if not s.passed_gate:
        if s.sharpe is None or abs(s.sharpe) < sharpe_thr: failures.append("low_sharpe")
        if s.fitness is None or abs(s.fitness) < fitness_thr: failures.append("low_fitness")
        if s.turnover is not None and s.turnover >= max_turn: failures.append("high_turnover")
        for code, value, label in (("high_prod_corr", s.prod_corr, "prod_corr"), ("high_self_corr", s.self_corr, "self_corr"), ("high_powerpool_corr", s.powerpool_corr, "powerpool_corr")):
            if value is not None and abs(value) >= max_corr: failures.append(code)
        if s.tests_failed: failures.append("failed_tests")
    if not failures and not s.passed_gate: failures.append("gate_failure")
    if s.passed_gate: diagnosis = "passed"
    elif "high_turnover" in failures: diagnosis = "turnover_problem"
    elif any(x in failures for x in ("high_prod_corr", "high_self_corr", "high_powerpool_corr")): diagnosis = "correlation_problem"
    elif "failed_tests" in failures: diagnosis = "validation_problem"
    elif "low_fitness" in failures: diagnosis = "fitness_problem"
    elif "low_sharpe" in failures: diagnosis = "signal_strength_problem"
    else: diagnosis = "general_gate_failure"
    return {"alpha_id": s.alpha_id, "diagnosis": diagnosis, "failure_codes": list(dict.fromkeys(failures)),
            "reasons": reasons, "thresholds":{"sharpe":sharpe_thr,"fitness":fitness_thr,"max_turnover":max_turn,"max_corr":max_corr},
            "metrics": {"sharpe": s.sharpe, "fitness": s.fitness, "turnover": s.turnover,
            "prod_corr": s.prod_corr, "self_corr": s.self_corr, "powerpool_corr": s.powerpool_corr,
            "tests_failed": s.tests_failed}, "expression": s.expression, "region": s.region,
            "settings": cfg or {"delay": s.delay, "universe": s.universe, "neutralization": s.neutralization}}


def diagnose(alpha_id: str) -> dict:
    with SessionLocal() as db:
        s = _sim(db, alpha_id)
        if not s:
            raise ValueError(f"No simulation result found for {alpha_id}.")
        return diagnose_sim_result(s)


def _replace_outer(expr: str, name: str, arg: str) -> str:
    return f"{name}({arg})"


def _has_call(expr: str, name: str) -> bool:
    return bool(re.search(rf"\b{re.escape(name)}\s*\(", expr, re.I))


def _set_time_window(expr,window):
 names={"ts_rank","ts_delta","ts_decay_linear","ts_mean","ts_std_dev","ts_zscore","ts_sum","ts_product"}
 for name in names:
  m=re.search(rf"\b{re.escape(name)}\s*\(",expr,re.I)
  if not m:continue
  start=m.end();depth=1;i=start;comma=None
  while i<len(expr) and depth:
   ch=expr[i]
   if ch=="(":depth+=1
   elif ch==")":depth-=1
   elif ch=="," and depth==1 and comma is None:comma=i
   i+=1
  if comma is None:continue
  tail=expr[comma+1:i-1];nm=re.search(r"(\d+)\s*$",tail)
  if nm:
   a=comma+1+nm.start(1);b=comma+1+nm.end(1);return expr[:a]+str(window)+expr[b:]
 return None


def _mutations(expr: str, diagnosis: str, settings: dict) -> list[dict]:
    """Conservative mutation catalogue. Every mutation changes one dimension and carries a reason."""
    out = []
    # Time horizon mutations. These are deliberately common, conservative windows.
    windows = [20, 40, 63, 126]
    if diagnosis in {"signal_strength_problem", "fitness_problem", "general_gate_failure", "turnover_problem"}:
        for w in windows:
            mutated=_set_time_window(expr,w)
            if mutated and re.sub(r"\s+","",mutated)!=re.sub(r"\s+","",expr):
                out.append({"type":"parameter","label":f"lookback_{w}","expression":mutated,"settings":{},"reason":f"Test a different time horizon ({w}) while preserving the signal structure."})
    # Structural transforms. Avoid nesting the same transform repeatedly.
    if diagnosis in {"signal_strength_problem", "fitness_problem", "general_gate_failure"}:
        if not _has_call(expr, "ts_rank"):
            for w in (63, 126): out.append({"type":"expression","label":f"ts_rank_{w}","expression":f"ts_rank({expr}, {w})","settings":{},"reason":"Add a time-series rank to test persistence/relative temporal strength."})
        if not _has_call(expr, "rank"):
            out.append({"type":"expression","label":"rank","expression":f"rank({expr})","settings":{},"reason":"Test cross-sectional normalization without changing the underlying signal."})
        if not _has_call(expr, "winsorize"):
            out.append({"type":"expression","label":"winsorize","expression":f"winsorize({expr})","settings":{},"reason":"Reduce the influence of extreme observations while preserving the signal."})
    if diagnosis == "turnover_problem":
        if not _has_call(expr, "ts_decay_linear"):
            for w in (20, 40, 63):
                out.append({"type":"expression","label":f"decay_{w}","expression":f"ts_decay_linear({expr}, {w})","settings":{},"reason":"Smooth the signal to test whether excess turnover is implementation noise."})
        # Settings variants are kept separate from expression changes.
        current = (settings.get("neutralization") or "").lower()
        for n in ("industry", "subindustry", "market"):
            if n != current:
                out.append({"type":"settings","label":f"neutralization_{n}","expression":expr,"settings":{"neutralization":n},"reason":f"Test {n} neutralization without changing the alpha expression."})
    if diagnosis == "correlation_problem":
        if not _has_call(expr, "group_rank"):
            out.append({"type":"expression","label":"industry_group_rank","expression":f"group_rank({expr}, industry)","settings":{},"reason":"Test an industry-relative construction to reduce shared market/industry exposure."})
        if not _has_call(expr, "ts_delta"):
            out.append({"type":"expression","label":"delta_63","expression":f"ts_delta({expr}, 63)","settings":{},"reason":"Test the change in the signal rather than its level to seek a more orthogonal representation."})
    if diagnosis == "validation_problem":
        # Validation failures are not safely auto-repaired here. Keep the branch for a targeted repair pass.
        out.append({"type":"repair","label":"validator_repair","expression":expr,"settings":{},"reason":"Send the exact validation failure back to the expression repair step; do not mutate blindly."})
    # De-duplicate exact proposals.
    seen=set(); clean=[]
    for x in out:
        key=(x["type"], re.sub(r"\s+", "", x["expression"]), json.dumps(x["settings"], sort_keys=True))
        if key not in seen:
            seen.add(key); clean.append(x)
    return clean


def _family_dict(db, f: M.AlphaFamily):
    variants = db.scalars(select(M.AlphaVariant).where(M.AlphaVariant.family_id == f.id).order_by(M.AlphaVariant.generation, M.AlphaVariant.created_at)).all()
    return {"id":f.id,"name":f.name,"status":f.status,"region":f.region,"hypothesis":_json(f.hypothesis_json,{}),
            "parent_alpha_id":f.parent_alpha_id,"generation":f.generation,"variant_budget":f.variant_budget,
            "variants_created":len(variants),"variants_passed":sum(v.status=="passed" for v in variants),
            "variants_failed":sum(v.status=="failed" for v in variants),"notes":f.notes,"created_at":f.created_at,
            "variants":[_variant_dict(v) for v in variants]}


def _variant_dict(v: M.AlphaVariant):
    return {"id":v.id,"family_id":v.family_id,"parent_variant_id":v.parent_variant_id,"parent_alpha_id":v.parent_alpha_id,
            "generation":v.generation,"mutation_type":v.mutation_type,"label":v.label,"expression":v.expression,
            "settings":_json(v.settings_json,{}),"reason":v.reason,"status":v.status,"alpha_id":v.alpha_id,
            "sim_result_id":v.sim_result_id,"fitness":v.fitness,"sharpe":v.sharpe,"turnover":v.turnover,
            "novelty":v.novelty,"created_at":v.created_at,"closed_reason":v.closed_reason}


def create_family(alpha_id: str, name: str = "", hypothesis: dict | None = None, budget: int = MAX_VARIANTS_PER_FAMILY):
    with SessionLocal() as db:
        s=_sim(db, alpha_id)
        if not s: raise ValueError(f"No simulation result found for {alpha_id}.")
        if s.passed_gate: raise ValueError("A passed alpha does not need a failure-evolution branch.")
        existing=db.scalar(select(M.AlphaFamily).where(M.AlphaFamily.parent_alpha_id==alpha_id, M.AlphaFamily.status=="open"))
        if existing: return _family_dict(db, existing)
        d=diagnose(alpha_id)
        f=M.AlphaFamily(name=name or f"Family for {alpha_id}", parent_alpha_id=alpha_id, region=s.region,
                        hypothesis_json=json.dumps(hypothesis or {}), status="open", generation=0,
                        variant_budget=max(1,min(MAX_VARIANTS_PER_FAMILY,int(budget))), notes=d["diagnosis"])
        db.add(f); db.flush()
        v=M.AlphaVariant(family_id=f.id,parent_variant_id=0,parent_alpha_id=alpha_id,generation=0,
                         mutation_type="parent",label="parent",expression=s.expression,
                         settings_json=json.dumps(d["settings"]),reason="Original failed alpha; preserved as lineage root.",status="failed",
                         alpha_id=alpha_id,sim_result_id=s.id,fitness=s.fitness,sharpe=s.sharpe,turnover=s.turnover,execution_key=s.execution_key or "")
        db.add(v); db.commit(); return _family_dict(db, f)


def evolve(family_id: int, max_variants: int = 10):
    with SessionLocal() as db:
        f=db.get(M.AlphaFamily,int(family_id))
        if not f: raise ValueError("alpha family not found")
        if f.status != "open": raise ValueError("alpha family is closed")
        variants=db.scalars(select(M.AlphaVariant).where(M.AlphaVariant.family_id==f.id).order_by(M.AlphaVariant.created_at.desc())).all()
        created=sum(1 for v in variants if v.mutation_type != "parent")
        if created >= f.variant_budget: raise ValueError("family variant budget exhausted")
        if f.generation >= MAX_GENERATIONS: raise ValueError("family generation limit reached")
        # Only empirically tested FAILED variants may generate further mutations. The lineage root
        # is itself a failed, tested simulation, so it is eligible for generation 1. Untested proposed
        # variants are never used as parents. A passed variant closes the search branch rather than
        # becoming an evidence-free mutation source.
        parent=next((v for v in variants if v.status=="failed" and v.sim_result_id),None)
        if parent is None:
            raise ValueError("No tested failed variant is available to evolve. Simulate a proposed variant first.")
        expr=parent.expression
        ps = db.get(M.SimResult, int(parent.sim_result_id)) if parent.sim_result_id else None
        if ps and parent.execution_key and ps.execution_key != parent.execution_key:
            ps = None
        if not ps:
            raise ValueError("Parent variant has no exact simulation result; it cannot be evolved.")
        d = diagnose_sim_result(ps)
        diag=d["diagnosis"]
        props=_mutations(expr,diag,_json(parent.settings_json,{}))
        room=min(max(1,int(max_variants)), f.variant_budget-created)
        # Don't exceed the next generation boundary.
        props=props[:room]
        for p in props:
            v=M.AlphaVariant(family_id=f.id,parent_variant_id=parent.id,parent_alpha_id=parent.parent_alpha_id,
                             generation=parent.generation+1,mutation_type=p["type"],label=p["label"],expression=p["expression"],
                             settings_json=json.dumps({**_json(parent.settings_json, {}), **p.get("settings", {})}),reason=p["reason"],status="proposed",execution_key=uuid.uuid4().hex)
            db.add(v)
        f.generation=max(f.generation,parent.generation+1); f.updated_at=_now(); db.commit()
        return _family_dict(db, f)


def list_families(limit=50, status=""):
    with SessionLocal() as db:
        q=select(M.AlphaFamily).order_by(M.AlphaFamily.updated_at.desc()).limit(max(1,min(int(limit),200)))
        if status: q=q.where(M.AlphaFamily.status==status)
        rows=db.scalars(q).all(); return [_family_dict(db,r) for r in rows]


def close_family(family_id:int, reason:str=""):
    with SessionLocal() as db:
        f=db.get(M.AlphaFamily,int(family_id))
        if not f: raise ValueError("alpha family not found")
        f.status="closed"; f.closed_reason=reason or "research branch closed"; f.updated_at=_now(); db.commit()
        return _family_dict(db,f)


def abandon_variant(variant_id:int, reason:str=""):
    with SessionLocal() as db:
        v=db.get(M.AlphaVariant,int(variant_id))
        if not v: raise ValueError("alpha variant not found")
        if v.status in ("passed","submitted"): raise ValueError("successful/submitted variants cannot be abandoned")
        v.status="abandoned"; v.closed_reason=reason or "discarded by researcher"; db.commit()
        return _variant_dict(v)


def attach_simulation_to_variant(variant_id:int, execution_key:str):
    with SessionLocal() as db:
        v=db.get(M.AlphaVariant,int(variant_id))
        if not v: raise ValueError("alpha variant not found")
        s=_sim_for_variant(db, variant_id, execution_key)
        if not s: raise ValueError(f"No simulation result found for execution {execution_key}.")
        v.alpha_id=s.alpha_id or v.alpha_id
        v.sim_result_id=s.id
        v.fitness=s.fitness; v.sharpe=s.sharpe; v.turnover=s.turnover
        v.status="passed" if s.passed_gate else "failed"
        v.closed_reason="" if s.passed_gate else "; ".join(_json(s.gate_reasons,[]))
        v.execution_key=execution_key
        db.commit()
        return {"matched":True,"variant":_variant_dict(v)}

def mark_variant_from_sim(alpha_id:str):
    # Backward-compatible attachment for simulations created outside evolution. It only matches
    # an explicit execution identity when one exists; expression matching is intentionally removed.
    with SessionLocal() as db:
        s=_sim(db,alpha_id)
        if not s: raise ValueError(f"No simulation result found for {alpha_id}.")
        if not s.execution_key or not s.variant_id:
            return {"matched":False,"alpha_id":alpha_id,"reason":"simulation has no evolution execution identity"}
        vid=s.variant_id; key=s.execution_key
    return attach_simulation_to_variant(vid,key)


def repair_variant(variant_id: int, max_tokens: int = 4000) -> dict:
    """Use the alpha-generation LLM only for variants explicitly marked repair. The proposed
    replacement must pass ACE's syntax/operator validator before it returns to the proposed state."""
    with SessionLocal() as db:
        v=db.get(M.AlphaVariant,int(variant_id))
        if not v: raise ValueError("alpha variant not found")
        if v.mutation_type != "repair": raise ValueError("only validator-repair variants can be repaired")
        if v.status in ("running", "submitted"): raise ValueError("variant is busy or already submitted")
        parent_sim=_sim_for_variant(db, v.parent_variant_id, "") if v.parent_variant_id else None
        failure = _json(parent_sim.gate_reasons, []) if parent_sim else []
        expr=v.expression
        v.status="running"; db.commit()
    from app.core.llm_router import TaskLLM
    prompt=("Repair the following WorldQuant BRAIN FastExpr expression. Return ONLY a JSON array with exactly one string. "
            "The replacement must preserve the intended signal as much as possible, fix syntax/operator/validation problems, "
            "and never invent fields.\nVALIDATION/FAILURE CONTEXT: " + json.dumps(failure) + "\nEXPRESSION: " + expr)
    try:
        res=TaskLLM("alpha_generation").generate_list(prompt,n=1,max_tokens=max_tokens)
        candidate=(res.expressions[0] if res.expressions else "").strip()
        if not candidate: raise ValueError("repair model returned no expression")
        from app.generation.service import sandbox_validate
        vr=sandbox_validate(candidate)
        if not vr.get("ok"):
            raise ValueError("repaired expression still fails validation: " + ", ".join(i.get("code", "") for i in vr.get("issues", [])))
        with SessionLocal() as db:
            vv=db.get(M.AlphaVariant,int(variant_id))
            vv.expression=candidate; vv.mutation_type="expression"; vv.label="validator_repair_fixed"; vv.reason="LLM repair passed ACE syntax/operator validation; requires simulation before further evolution."; vv.status="proposed"; vv.closed_reason=""; db.commit()
            return _variant_dict(vv)
    except Exception as exc:
        with SessionLocal() as db:
            vv=db.get(M.AlphaVariant,int(variant_id))
            if vv: vv.status="repair_required"; vv.closed_reason=str(exc)[:500]; db.commit()
        raise
