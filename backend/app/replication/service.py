"""Alpha portability / replication engine.

Turns a proven source-region alpha into target-region candidates without blindly replacing
region names. The engine preserves expression structure, verifies target fields against BRAIN,
and can rank conceptual replacements using descriptions/categories. Optional LLM ranking is
used only to explain/rank candidates; it never creates an unverified field.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter
from difflib import SequenceMatcher

from sqlalchemy import select
from app.db.base import SessionLocal
from app.db import models as M
from app.brain import engine
from app.generation import service as gen

GROUP_IDENTS = set(getattr(gen, "_GROUP_IDENTS", set()))
UNIVERSAL = {
    "open", "close", "high", "low", "volume", "vwap", "returns", "cap", "adv20", "sharesout",
    "dividend", "split", "assets", "liabilities", "sales", "ebit", "ebitda", "industry", "subindustry",
    "sector", "market", "country", "exchange", "currency", "sedol",
}


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _field_score(source: dict, target: dict) -> tuple[float, list[str]]:
    src_text = " ".join([source.get("id", ""), source.get("description", ""), source.get("category", "")])
    tgt_text = " ".join([target.get("id", ""), target.get("description", ""), target.get("category", "")])
    a, b = _tokens(src_text), _tokens(tgt_text)
    overlap = a & b
    desc_a, desc_b = _tokens(source.get("description", "")), _tokens(target.get("description", ""))
    desc_overlap = desc_a & desc_b
    id_ratio = SequenceMatcher(None, str(source.get("id", "")).lower(), str(target.get("id", "")).lower()).ratio()
    cat_bonus = 1.5 if source.get("category") and source.get("category") == target.get("category") else 0.0
    score = len(overlap) * 1.2 + len(desc_overlap) * 1.5 + id_ratio * 2.0 + cat_bonus
    return round(score, 3), sorted(overlap | desc_overlap)[:16]


def _lookup_cached(field_id: str, region: str, delay: int) -> dict | None:
    with SessionLocal() as db:
        row = db.scalar(select(M.Field).where(M.Field.field_id == field_id, M.Field.region == region, M.Field.delay == int(delay)).order_by(M.Field.last_seen.desc()))
        if not row:
            return None
        ds = db.scalar(select(M.Dataset).where(M.Dataset.dataset_id == row.dataset_id, M.Dataset.region == region, M.Dataset.delay == int(delay)))
        return {"id": row.field_id, "dataset_id": row.dataset_id, "region": row.region, "delay": row.delay,
                "type": row.type, "description": row.description, "category": ds.category if ds else "", "name": ds.name if ds else ""}


def _search_brain(field_id: str, region: str, delay: int, universe: str) -> list[dict]:
    """Search BRAIN's region-specific field catalogue without guessing a dataset id."""
    try:
        s = engine.require_session()
        region, delay, universe = engine.valid_combo("EQUITY", region, delay, universe)
        df = engine.brain_call(f"target field {field_id}", engine.ace.get_datafields, s,
                               instrument_type="EQUITY", region=region, delay=delay,
                               universe=universe, dataset_id="", data_type="ALL", search=field_id)
        if df is None or getattr(df, "empty", True):
            return []
        rows = engine._json_safe(df.to_dict(orient="records"))
        out = []
        for r in rows:
            rid = str(r.get("id", ""))
            if not rid:
                continue
            out.append({"id": rid, "dataset_id": str(r.get("dataset_id") or r.get("dataset.id") or ""),
                        "region": region, "delay": delay, "type": str(r.get("type", "MATRIX")),
                        "description": str(r.get("description", "")),
                        "category": str(r.get("category_name") or r.get("category_id") or ""),
                        "name": str(r.get("dataset_name") or "")})
        return out
    except Exception:
        return []


def _target_candidates(source: dict, target_region: str, delay: int, universe: str, mode: str, limit: int = 8) -> list[dict]:
    # Exact target lookup first.
    exact = _lookup_cached(source["id"], target_region, delay)
    if exact:
        return [{"field": exact, "score": 100.0, "matched_terms": ["exact_id"], "kind": "exact"}]

    brain_rows = _search_brain(source["id"], target_region, delay, universe)
    if brain_rows:
        rows = []
        for t in brain_rows:
            score, terms = _field_score(source, t)
            rows.append({"field": t, "score": score + (50 if t["id"] == source["id"] else 0), "matched_terms": terms, "kind": "equivalent"})
        rows.sort(key=lambda x: (-x["score"], x["field"]["id"]))
        return rows[:limit]

    # Search the local target catalogue if the API search didn't find an id-level match.
    with SessionLocal() as db:
        rows = db.scalars(select(M.Field).where(M.Field.region == target_region, M.Field.delay == int(delay)).limit(5000)).all()
        ds_ids = {r.dataset_id for r in rows}
        ds_map = {d.dataset_id: d for d in db.scalars(select(M.Dataset).where(M.Dataset.region == target_region, M.Dataset.delay == int(delay))).all()}
    ranked = []
    for r in rows:
        ds = ds_map.get(r.dataset_id)
        t = {"id": r.field_id, "dataset_id": r.dataset_id, "region": r.region, "delay": r.delay, "type": r.type,
             "description": r.description, "category": ds.category if ds else "", "name": ds.name if ds else ""}
        score, terms = _field_score(source, t)
        ranked.append({"field": t, "score": score, "matched_terms": terms, "kind": "concept"})
    ranked.sort(key=lambda x: (-x["score"], x["field"]["id"]))
    return ranked[:limit]


def _replace_fields(expr: str, mapping: dict[str, str]) -> str:
    out = expr
    for src, dst in sorted(mapping.items(), key=lambda x: -len(x[0])):
        out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(src)}(?![A-Za-z0-9_])", dst, out)
    return out


def _extract_fields(expr: str) -> list[str]:
    names = gen._leaf_idents(expr)
    return [n for n in names if n.lower() not in GROUP_IDENTS and n.lower() not in UNIVERSAL]


def _source_metadata(field_ids: list[str], region: str, delay: int, universe: str) -> list[dict]:
    out = []
    for fid in field_ids:
        f = _lookup_cached(fid, region, delay)
        if f:
            out.append(f)
            continue
        # If the field is not cached, query the source region too. This is metadata only.
        rows = _search_brain(fid, region, delay, universe)
        exact = next((x for x in rows if x["id"] == fid), rows[0] if rows else None)
        out.append(exact or {"id": fid, "dataset_id": "", "region": region, "delay": delay, "type": "MATRIX", "description": "", "category": "", "name": ""})
    return out


def _llm_rank(source_expression: str, source_region: str, target_region: str, candidates: list[dict]) -> dict:
    if not candidates:
        return {}
    try:
        from app.core.llm_router import TaskLLM
        compact = [{"id": c["field"]["id"], "description": c["field"].get("description", ""), "category": c["field"].get("category", ""), "score": c["score"]} for c in candidates[:12]]
        prompt = ("You are ranking verified BRAIN field candidates for cross-region alpha replication. "
                  "Do not invent fields and do not add any id not present in CANDIDATES. Explain the economic "
                  "conceptual similarity. Return ONLY a JSON array containing one JSON object with keys "
                  "best_id, confidence, rationale, alternatives.\n"
                  f"SOURCE REGION: {source_region}\nTARGET REGION: {target_region}\nEXPRESSION: {source_expression}\n"
                  f"CANDIDATES: {json.dumps(compact, ensure_ascii=False)}")
        res = TaskLLM("research").generate_list(prompt, n=1, max_tokens=900)
        raw = res.expressions[0] if res.expressions else ""
        obj = json.loads(raw)
        if isinstance(obj, list): obj = obj[0] if obj else {}
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def preview(req: dict) -> dict:
    expr = str(req.get("expression", "")).strip()
    if not expr:
        raise ValueError("Alpha expression is empty.")
    source_region = str(req.get("source_region", "IND")).upper()
    target_region = str(req.get("target_region", "GBR")).upper()
    source_delay = int(req.get("source_delay", 1)); target_delay = int(req.get("target_delay", source_delay))
    source_universe = str(req.get("source_universe", "TOP1000")); target_universe = str(req.get("target_universe", source_universe))
    mode = str(req.get("mode", "concept"))
    try:
        vr = gen.sandbox_validate(expr)
        syntax_ok = bool(vr.get("ok"))
    except Exception as e:
        vr = {"ok": False, "issues": [{"code": "VALIDATION", "message": str(e)}]}; syntax_ok = False
    if not syntax_ok:
        raise ValueError("Source expression failed validation: " + "; ".join(i.get("message", "") for i in vr.get("issues", [])))
    fields = _extract_fields(expr)
    source_fields = _source_metadata(fields, source_region, source_delay, source_universe)
    all_maps = {}
    candidate_rows = []
    for sf in source_fields:
        cands = _target_candidates(sf, target_region, target_delay, target_universe, mode)
        all_maps[sf["id"]] = cands
        candidate_rows.append({"source": sf, "candidates": cands})
    exact_possible = bool(fields) and all(all_maps.get(fid) and all_maps[fid][0]["kind"] == "exact" for fid in fields)
    candidates = []
    if fields:
        # Cartesian product is capped; each field contributes its top three mappings.
        pools = [rows["candidates"][:3] for rows in candidate_rows]
        combos = [[]]
        for pool in pools:
            combos = [a + [x] for a in combos for x in pool][:24]
        for combo in combos:
            mapping = {fields[i]: combo[i]["field"]["id"] for i in range(len(fields))}
            if len(set(mapping.values())) != len(mapping):
                continue
            expression = _replace_fields(expr, mapping)
            try:
                check = gen.sandbox_validate(expression)
            except Exception as e:
                check = {"ok": False, "issues": [{"code": "VALIDATION", "message": str(e)}]}
            candidates.append({"expression": expression, "mapping": mapping, "verified_fields": [x["field"] for x in combo],
                              "score": round(sum(x["score"] for x in combo), 3), "valid": bool(check.get("ok")),
                              "issues": check.get("issues", []), "kind": "exact" if all(x["kind"] == "exact" for x in combo) else "equivalent"})
    candidates.sort(key=lambda x: (-int(x["valid"]), -x["score"], x["expression"]))
    llm = _llm_rank(expr, source_region, target_region, candidates) if candidates and not exact_possible else {}
    if llm.get("best_id"):
        for c in candidates:
            if any(f["id"] == llm["best_id"] for f in c["verified_fields"]): c["llm_recommended"] = True
    return {"source": {"region": source_region, "delay": source_delay, "universe": source_universe, "expression": expr, "fields": source_fields},
            "target": {"region": target_region, "delay": target_delay, "universe": target_universe},
            "mode": mode, "exact_possible": exact_possible, "field_maps": candidate_rows,
            "candidates": candidates[:12], "llm_review": llm,
            "notes": ["Exact field IDs are preferred.", "Equivalent/conceptual replacements are only proposed from fields verified in the target-region catalogue.", "Simulation is required before treating a replication as successful."]}
