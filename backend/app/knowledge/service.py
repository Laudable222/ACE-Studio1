"""Knowledge service — the studio's memory and cross-region intelligence.

Every dataset/field fetch is ingested here, so the DB steadily learns the whole data
landscape. Similarity is computed WITHOUT any heavy embedding model (fully offline):
a lightweight token cosine over name+description, plus the strong exact-id-across-regions
signal that BRAIN's shared dataset/field ids give us. That is enough to answer "which
datasets/fields are the same concept in other regions" — the core of cross-region
research — with zero extra dependencies.
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter

from sqlalchemy import func, select

from app.db.base import SessionLocal
from app.db import models as M

DELIM = "|||"
_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "and", "or", "for", "to", "in", "on", "by", "with",
         "data", "dataset", "value", "score", "field", "based", "is", "are", "this"}


def _tokens(*parts: str) -> Counter:
    text = " ".join(p or "" for p in parts).lower()
    return Counter(t for t in _TOKEN.findall(text) if t not in _STOP and len(t) > 1)


def _weighted(name: str, desc: str, w_name: int = 3) -> Counter:
    """A token vector that weights the dataset NAME higher than its description. Names are the
    curated, high-signal label; descriptions share a lot of generic finance vocabulary, so
    without this a broker-estimates set and a news set look related just from boilerplate words.
    Weighting the name ~3x makes matches reflect what the dataset actually IS."""
    c: Counter = Counter()
    for t, n in _tokens(name).items():
        c[t] += n * w_name
    for t, n in _tokens(desc).items():
        c[t] += n
    return c


def _shared(a: Counter, b: Counter, top: int = 6) -> list:
    """The overlapping tokens that drove a match, strongest first — surfaced so the UI can show
    WHY two datasets were linked instead of an opaque score."""
    common = set(a) & set(b)
    return sorted(common, key=lambda t: -(a[t] + b[t]))[:top]


def _cosine(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[t] * b[t] for t in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


# ── ingestion ────────────────────────────────────────────────────────────────────────

def ingest_datasets(region: str, delay: int, instrument: str, universe: str, rows: list) -> None:
    if not rows:
        return
    now = time.time()
    with SessionLocal() as db:
        for r in rows:
            did = str(r.get("id") or "").strip()
            if not did:
                continue
            existing = db.scalar(select(M.Dataset).where(
                M.Dataset.dataset_id == did, M.Dataset.region == region, M.Dataset.delay == delay))
            if existing:
                existing.fetch_count += 1
                existing.last_seen = now
                existing.instrument = str(instrument or existing.instrument or "EQUITY")
                existing.universe = str(universe or existing.universe or "")
                existing.category = str(r.get("category_id") or existing.category or "")
                if r.get("name") is not None:
                    existing.name = str(r.get("name") or existing.name or "")
                if r.get("description") is not None:
                    existing.description = str(r.get("description") or existing.description or "")
                existing.coverage = float(r.get("coverage") or existing.coverage or 0)
                existing.value_score = float(r.get("valueScore") or existing.value_score or 0)
                existing.alpha_count = int(r.get("alphaCount") or existing.alpha_count or 0)
            else:
                db.add(M.Dataset(
                    dataset_id=did, region=region, delay=delay, instrument=instrument,
                    universe=universe, category=str(r.get("category_id") or ""),
                    name=str(r.get("name") or ""), description=str(r.get("description") or ""),
                    coverage=float(r.get("coverage") or 0), value_score=float(r.get("valueScore") or 0),
                    alpha_count=int(r.get("alphaCount") or 0)))
        db.commit()


def ingest_fields(region: str, delay: int, dataset_ids: list, rows: list) -> None:
    if not rows:
        return
    now = time.time()
    with SessionLocal() as db:
        for r in rows:
            fid = str(r.get("id") or "").strip()
            if not fid:
                continue
            existing = db.scalar(select(M.Field).where(
                M.Field.field_id == fid, M.Field.dataset_id == str(r.get("dataset_id") or (dataset_ids[0] if dataset_ids else "")), M.Field.region == region, M.Field.delay == delay))
            if existing:
                existing.last_seen = now
            else:
                _pfx = r.get("prefix") or (fid.split("_", 1)[0] if "_" in fid else "general")
                db.add(M.Field(
                    field_id=fid, dataset_id=str(r.get("dataset_id") or (dataset_ids[0] if dataset_ids else "")),
                    region=region, delay=delay, type=str(r.get("type") or "MATRIX").upper(),
                    prefix=str(_pfx).lower(),
                    description=str(r.get("description") or ""),
                    alpha_count=int(r.get("alphaCount") or 0), is_virgin=bool(r.get("is_virgin"))))
        db.commit()


# ── queries ──────────────────────────────────────────────────────────────────────────

def catalogue_datasets(region: str, delay: int, instrument: str = "", universe: str = "",
                       limit: int = 5000) -> list:
    """Return datasets already ingested by Data Explorer for the requested context.

    This is deliberately local-only. It never calls BRAIN. Data Explorer is responsible
    for refreshing the catalogue through ingest_datasets().
    """
    with SessionLocal() as db:
        q = select(M.Dataset).where(
            M.Dataset.region == str(region),
            M.Dataset.delay == int(delay),
        )
        if instrument:
            q = q.where(M.Dataset.instrument == str(instrument))
        if universe:
            q = q.where(M.Dataset.universe == str(universe))
        q = q.order_by(M.Dataset.dataset_id).limit(max(1, min(int(limit), 10000)))
        rows = db.scalars(q).all()

    return [{
        "id": r.dataset_id,
        "name": r.name,
        "description": r.description,
        "category": r.category,
        "coverage": r.coverage,
        "valueScore": r.value_score,
        "alphaCount": r.alpha_count,
        "region": r.region,
        "delay": r.delay,
        "instrument": r.instrument,
        "universe": r.universe,
    } for r in rows]


def catalogue_fields(dataset_ids: list, region: str, delay: int,
                     limit: int = 20000) -> list:
    """Return fields already ingested for the supplied local catalogue datasets.

    Fields are constrained by dataset_id, region and delay. Universe/instrument are
    intentionally inherited from the already-filtered Dataset rows. This avoids treating
    Field as if it had configuration columns it does not store.
    """
    ids = [str(x).strip() for x in (dataset_ids or []) if str(x).strip()]
    if not ids:
        return []
    with SessionLocal() as db:
        q = select(M.Field).where(
            M.Field.dataset_id.in_(ids),
            M.Field.region == str(region),
            M.Field.delay == int(delay),
        ).order_by(M.Field.dataset_id, M.Field.field_id).limit(max(1, min(int(limit), 50000)))
        rows = db.scalars(q).all()

    return [{
        "id": r.field_id,
        "dataset_id": r.dataset_id,
        "region": r.region,
        "delay": r.delay,
        "type": r.type,
        "prefix": r.prefix,
        "description": r.description,
        "alphaCount": r.alpha_count,
        "is_virgin": r.is_virgin,
    } for r in rows]


def fields_by_refs(refs: list, region: str, delay: int) -> list:
    """Resolve saved (field_id, dataset_id) references back to full catalogue rows.

    Each ref is normally {"id": field_id, "dataset_id": dataset_id, ...} — the shape saved
    with an Experiment. For experiments created before dataset_id was saved alongside the
    field id, a ref may just be a bare field_id string; those are resolved by id only
    (region/delay scoped), which can occasionally match more than one dataset if the same
    field id is reused across datasets — an unavoidable ambiguity for that legacy shape.
    """
    pairs: set[tuple[str, str]] = set()
    id_only: set[str] = set()
    all_ids: set[str] = set()
    for r in (refs or []):
        if isinstance(r, dict):
            fid = str(r.get("id") or "").strip()
            dsid = str(r.get("dataset_id") or "").strip()
        else:
            fid, dsid = str(r or "").strip(), ""
        if not fid:
            continue
        all_ids.add(fid)
        if dsid:
            pairs.add((fid, dsid))
        else:
            id_only.add(fid)
    if not all_ids:
        return []
    with SessionLocal() as db:
        rows = db.scalars(select(M.Field).where(
            M.Field.field_id.in_(all_ids),
            M.Field.region == str(region),
            M.Field.delay == int(delay),
        )).all()
    seen: set[tuple[str, str]] = set()
    out = []
    for r in rows:
        key = (r.field_id, r.dataset_id)
        if key not in pairs and r.field_id not in id_only:
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": r.field_id, "dataset_id": r.dataset_id, "region": r.region, "delay": r.delay,
            "type": r.type, "prefix": r.prefix, "description": r.description,
            "alphaCount": r.alpha_count, "is_virgin": r.is_virgin,
        })
    return out


def dataset_categories(dataset_ids: list, region: str, delay: int) -> dict:
    """dataset_id -> category, for the datasets already catalogued in this region/delay."""
    ids = [str(x).strip() for x in (dataset_ids or []) if str(x).strip()]
    if not ids:
        return {}
    with SessionLocal() as db:
        rows = db.scalars(select(M.Dataset).where(
            M.Dataset.dataset_id.in_(ids), M.Dataset.region == str(region), M.Dataset.delay == int(delay),
        )).all()
    return {r.dataset_id: r.category for r in rows if r.category}


    with SessionLocal() as db:
        n_ds = db.scalar(select(func.count()).select_from(M.Dataset)) or 0
        n_fields = db.scalar(select(func.count()).select_from(M.Field)) or 0
        regions = [r for (r,) in db.execute(select(M.Dataset.region).distinct())]
        # datasets seen in more than one region (by shared id) = cross-region concepts
        by_id = db.execute(
            select(M.Dataset.dataset_id, func.count(func.distinct(M.Dataset.region)).label("nreg"))
            .group_by(M.Dataset.dataset_id)).all()
        cross = [d for d, nreg in by_id if nreg > 1]
        cats = db.execute(
            select(M.Dataset.category, func.count()).where(M.Dataset.category != "")
            .group_by(M.Dataset.category)).all()
    return {
        "datasets": n_ds, "fields": n_fields, "regions": sorted(regions),
        "cross_region_concepts": len(cross),
        "categories": sorted([{"category": c, "count": n} for c, n in cats],
                             key=lambda x: -x["count"]),
    }


def judge_relationships(base_id: str, region: str, delay: int, candidates: list, progress=None) -> dict:
    """AI check over lexical candidates: the cosine step finds datasets whose NAMES/DESCRIPTIONS
    overlap, which can surface coincidental word matches. This asks an LLM whether each candidate
    is GENUINELY the same or a closely related concept for quant research, with a one-line reason —
    so nonsensical links are dropped rather than shown just because they share words."""
    from app.brain import engine  # noqa: F401 — ensures the vendored LLM stack is importable
    import llm_providers as L
    import keys as keymgr

    with SessionLocal() as db:
        base = db.scalar(select(M.Dataset).where(M.Dataset.dataset_id == base_id))
    base_name = base.name if base else base_id
    base_desc = (base.description if base else "") or ""

    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("critique")
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings to verify relationships.")
    if not candidates:
        return {"judged": []}
    lst = "\n".join(f"{i + 1}. {c.get('name') or c.get('dataset_id')} "
                    f"[{c.get('region', region)}] — {(c.get('description') or c.get('category') or '')[:90]}"
                    for i, c in enumerate(candidates))
    meta = (
        "You are validating candidate relationships between WorldQuant BRAIN datasets. A lexical filter "
        "already proposed these because their names/descriptions overlap, but some overlaps are "
        "coincidental. For EACH candidate decide whether it genuinely represents the SAME concept or a "
        "closely related one that a quant could reasonably substitute or pair, versus an unrelated dataset "
        "that merely shares words.\n"
        f"BASE DATASET: {base_name} — {base_desc[:120]}\n"
        f"CANDIDATES:\n{lst}\n"
        f"Return ONLY a JSON array of {len(candidates)} strings, each: index ||| yes|no ||| short reason\n")
    if progress:
        progress(message=f"verifying {len(candidates)} relationships with AI…")
    from app.core import llm_cache
    res = llm_cache.cached_generate_list("critique", meta, n=len(candidates))
    verdicts = {}
    for s in res.expressions:
        parts = [p.strip() for p in str(s).split("|||")]
        if len(parts) >= 2:
            try:
                idx = int("".join(ch for ch in parts[0] if ch.isdigit())) - 1
            except ValueError:
                continue
            verdicts[idx] = {"related": parts[1].lower().startswith("y"),
                             "reason": parts[2] if len(parts) > 2 else ""}
    judged = []
    for i, c in enumerate(candidates):
        v = verdicts.get(i, {"related": False, "reason": "AI returned no verdict; relationship remains unverified."})
        judged.append({**c, "ai_related": v["related"], "ai_reason": v["reason"], "ai_verdict_status": "verified" if i in verdicts else "unknown"})
    return {"judged": judged, "provider": res.provider}


def cross_region_concepts(min_regions: int = 2, limit: int = 60) -> list:
    """Datasets whose SAME id is catalogued in >= min_regions regions — the reliable cross-region
    concepts (exact id match, no lexical guessing)."""
    with SessionLocal() as db:
        rows = db.scalars(select(M.Dataset)).all()
    by_id: dict = {}
    for r in rows:
        e = by_id.setdefault(r.dataset_id, {"dataset_id": r.dataset_id, "name": r.name,
                                            "category": r.category, "regions": set()})
        e["regions"].add(r.region)
    out = [{**v, "regions": sorted(v["regions"])} for v in by_id.values() if len(v["regions"]) >= min_regions]
    out.sort(key=lambda x: -len(x["regions"]))
    return out[:limit]


def analyze_cross_region(min_regions: int = 2, progress=None) -> dict:
    """AI pass over the cross-region concepts: rate how worthwhile each is to research across its
    regions (to diversify / test faster) and why, based on its CATEGORY — not word overlap."""
    concepts = cross_region_concepts(min_regions)
    n_regions = 0
    with SessionLocal() as db:
        n_regions = db.scalar(select(func.count(func.distinct(M.Dataset.region)))) or 0
    if n_regions < min_regions:
        return {"enough_regions": False, "regions_known": n_regions, "concepts": []}
    if not concepts:
        return {"enough_regions": True, "regions_known": n_regions, "concepts": [], "note": "No shared cross-region concepts yet."}
    from app.brain import engine  # noqa: F401
    import llm_providers as L
    import keys as keymgr
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("critique")
    if not chain:
        return {"enough_regions": True, "regions_known": n_regions, "concepts": concepts,
                "note": "Add an LLM key in Settings for the AI rating."}
    take = concepts[:40]
    lst = "\n".join(f"{i + 1}. {c['name'] or c['dataset_id']} [{c['category'] or 'uncategorised'}] "
                    f"in regions {', '.join(c['regions'])}" for i, c in enumerate(take))
    meta = (
        "You advise which CROSS-REGION data concepts are most worth exploiting on WorldQuant BRAIN. Each item "
        "below is the SAME dataset available in several regions. Judge — from its data CATEGORY and how that "
        "kind of signal typically travels across markets — how promising it is to research the same idea across "
        "those regions to diversify and test faster. Do NOT rate on the name wording; rate on the mechanism.\n"
        + lst +
        f"\nReturn ONLY a JSON array of {len(take)} strings, each: index {DELIM} high|medium|low {DELIM} one-line reason\n")
    if progress:
        progress(message=f"rating {len(take)} cross-region concepts…")
    from app.core import llm_cache
    res = llm_cache.cached_generate_list("critique", meta, n=len(take))
    ranked = {}
    for s in res.expressions:
        parts = [p.strip() for p in str(s).split(DELIM)]
        if len(parts) >= 2:
            try:
                idx = int("".join(ch for ch in parts[0] if ch.isdigit())) - 1
            except ValueError:
                continue
            ranked[idx] = {"rating": parts[1].lower(), "reason": parts[2] if len(parts) > 2 else ""}
    out = []
    for i, c in enumerate(take):
        out.append({**c, **ranked.get(i, {"rating": "", "reason": ""})})
    order = {"high": 0, "medium": 1, "low": 2, "": 3}
    out.sort(key=lambda x: order.get(x.get("rating", ""), 3))
    return {"enough_regions": True, "regions_known": n_regions, "concepts": out, "provider": res.provider}


def datasets_in_category(category: str, region: str = "", limit: int = 300) -> list:
    """Every catalogued dataset in a category (deduped by id) — so Strategy Atlas can explore
    the WHOLE category rather than a single dataset."""
    with SessionLocal() as db:
        q = select(M.Dataset).where(M.Dataset.category == category)
        if region:
            q = q.where(M.Dataset.region == region)
        rows = db.scalars(q.limit(limit * 2)).all()
    seen, out = set(), []
    for r in rows:
        if r.dataset_id in seen:
            continue
        seen.add(r.dataset_id)
        out.append({"id": r.dataset_id, "name": r.name, "description": r.description})
        if len(out) >= limit:
            break
    return out


def dataset_twins(dataset_id: str, region: str, delay: int, min_score: float = 0.45) -> dict:
    """Find the same concept in OTHER regions: exact-id matches first (strongest), then
    name/description-similar datasets above a threshold."""
    with SessionLocal() as db:
        base = db.scalar(select(M.Dataset).where(
            M.Dataset.dataset_id == dataset_id, M.Dataset.region == region, M.Dataset.delay == delay))
        if base is None:
            base = db.scalar(select(M.Dataset).where(M.Dataset.dataset_id == dataset_id))
        if base is None:
            return {"dataset_id": dataset_id, "twins": []}
        base_tok = _tokens(base.name, base.description)
        base_cat = (base.category or "").strip().lower()
        others = db.scalars(select(M.Dataset).where(M.Dataset.region != region, M.Dataset.delay == base.delay)).all()
        n_regions = db.scalar(select(func.count(func.distinct(M.Dataset.region)))) or 0
        twins = []
        for o in others:
            exact = o.dataset_id == dataset_id
            # Only compare WITHIN the same category (or an exact id match). A shared word across
            # different categories — "analyst" text appearing in a sentiment dataset — must NOT
            # create a bogus twin, so cross-category lexical matches are excluded up front.
            same_cat = bool(base_cat) and (o.category or "").strip().lower() == base_cat
            if not exact and not same_cat:
                continue
            score = 1.0 if exact else _cosine(base_tok, _tokens(o.name, o.description))
            if exact or score >= min_score:
                twins.append({"dataset_id": o.dataset_id, "region": o.region, "delay": o.delay,
                              "name": o.name, "category": o.category, "exact": exact,
                              "score": round(score, 3)})
        twins.sort(key=lambda t: (-t["exact"], -t["score"]))
        return {"dataset_id": dataset_id, "region": region, "regions_known": n_regions,
                "name": base.name, "category": base.category, "twins": twins[:30]}


def field_equivalents(field_id: str, region: str, delay: int) -> dict:
    """Fields that represent the same signal in other regions — exact id first, then
    same-prefix/type/description-similar fields."""
    with SessionLocal() as db:
        base = db.scalar(select(M.Field).where(
            M.Field.field_id == field_id, M.Field.region == region, M.Field.delay == delay)) or \
               db.scalar(select(M.Field).where(M.Field.field_id == field_id, M.Field.region == region)) or \
               db.scalar(select(M.Field).where(M.Field.field_id == field_id, M.Field.delay == delay))
        if base is None:
            return {"field_id": field_id, "equivalents": []}
        base_tok = _tokens(base.description)
        others = db.scalars(select(M.Field).where(M.Field.region != region, M.Field.delay == base.delay)).all()
        out = []
        for o in others:
            exact = o.field_id == field_id
            score = 1.0 if exact else (
                _cosine(base_tok, _tokens(o.description)) if o.type == base.type and o.prefix == base.prefix else 0.0)
            if exact or score >= 0.5:
                out.append({"field_id": o.field_id, "region": o.region, "type": o.type,
                            "exact": exact, "score": round(score, 3)})
        out.sort(key=lambda t: (-t["exact"], -t["score"]))
        return {"field_id": field_id, "region": region, "type": base.type, "equivalents": out[:30]}


def similar_datasets(dataset_id: str, region: str, delay: int, top: int = 8) -> list:
    """Datasets in the SAME region most similar in concept — helps spread research across
    related-but-different data rather than fixating on one dataset."""
    with SessionLocal() as db:
        base = db.scalar(select(M.Dataset).where(
            M.Dataset.dataset_id == dataset_id, M.Dataset.region == region, M.Dataset.delay == delay)) or \
               db.scalar(select(M.Dataset).where(M.Dataset.dataset_id == dataset_id, M.Dataset.region == region))
        if base is None:
            return []
        base_vec = _weighted(base.name, base.description)
        rows = db.scalars(select(M.Dataset).where(
            M.Dataset.region == region, M.Dataset.dataset_id != dataset_id)).all()
        scored = []
        for r in rows:
            o_vec = _weighted(r.name, r.description)
            scored.append({"dataset_id": r.dataset_id, "name": r.name, "category": r.category,
                           "score": round(_cosine(base_vec, o_vec), 3),
                           "shared": _shared(base_vec, o_vec)})
        scored.sort(key=lambda x: -x["score"])
        return [s for s in scored if s["score"] > 0.2][:top]


def memory_dict(x):
 import json
 return {"id":x.id,"created_at":x.created_at,"updated_at":x.updated_at,"type":x.item_type,"title":x.title,"content":x.content,"region":x.region,"dataset":x.dataset,"field":x.field,"operator":x.operator,"tags":json.loads(x.tags_json or "[]"),"confidence":x.confidence,"source":x.source,"evidence":json.loads(x.evidence_json or "{}"),"status":x.status}

def add_memory(item_type,title,content,region="",dataset="",field="",operator="",tags=None,confidence="unverified",source="user",evidence=None):
 import json
 with SessionLocal() as db:
  x=M.KnowledgeItem(item_type=item_type,title=title.strip(),content=content.strip(),region=region.strip(),dataset=dataset.strip(),field=field.strip(),operator=operator.strip(),tags_json=json.dumps(tags or []),confidence=confidence,source=source,evidence_json=json.dumps(evidence or {})); db.add(x); db.commit(); db.refresh(x); return memory_dict(x)

def delete_memory(item_id:int) -> bool:
 with SessionLocal() as db:
  x=db.get(M.KnowledgeItem,int(item_id))
  if not x: return False
  db.delete(x); db.commit(); return True

def list_memories(limit=100,item_type="",region="",q=""):
 with SessionLocal() as db:
  stmt=select(M.KnowledgeItem).order_by(M.KnowledgeItem.updated_at.desc()).limit(max(1,min(int(limit),500)))
  if item_type:stmt=stmt.where(M.KnowledgeItem.item_type==item_type)
  if region:stmt=stmt.where((M.KnowledgeItem.region==region)|(M.KnowledgeItem.region==""))
  rows=db.scalars(stmt).all()
 q=q.lower().strip()
 if q:rows=[x for x in rows if q in (x.title+" "+x.content+" "+x.field+" "+x.dataset+" "+x.operator).lower()]
 return [memory_dict(x) for x in rows]

def retrieve_memories(query,region="",field="",dataset="",limit=8):
 terms=_tokens(query,field,dataset); candidates=list_memories(500,region=region); scored=[]
 for x in candidates:
  text=(x["title"]+" "+x["content"]+" "+x["field"]+" "+x["dataset"]+" "+" ".join(x["tags"])).lower(); overlap=sum(1 for t in terms if t and t in text)
  if x["region"]==region and region:overlap+=2
  if field and x["field"]==field:overlap+=4
  if dataset and x["dataset"]==dataset:overlap+=4
  # A genuinely general tip (no region/field/dataset scoping at all — "always applies") gets a
  # small floor so it can still surface on modest topical overlap, not just heavy exact matches.
  if not x["region"] and not x["field"] and not x["dataset"]:overlap+=1
  if overlap:scored.append((overlap,x))
 scored.sort(key=lambda z:(-z[0],-z[1]["updated_at"])); return [x for _,x in scored[:limit]]


def memory_prompt_context(query: str, region: str = "", fields: list | None = None, datasets: list | None = None, limit: int = 8) -> str:
    """Return only relevant, user-authored/research memory as a compact prompt section. Memory is
    advisory evidence, never an instruction that can override system/datafield/operator rules."""
    field_ids = [str(f.get("id", f) if isinstance(f, dict) else f) for f in (fields or [])]
    dataset_ids = [str(d.get("id", d) if isinstance(d, dict) else d) for d in (datasets or [])]
    items = []
    seen=set()
    for fid in field_ids[:30]:
        for x in retrieve_memories(query, region=region, field=fid, limit=limit):
            if x["id"] not in seen: seen.add(x["id"]); items.append(x)
    for did in dataset_ids[:20]:
        for x in retrieve_memories(query, region=region, dataset=did, limit=limit):
            if x["id"] not in seen: seen.add(x["id"]); items.append(x)
    # General (not field/dataset-tagged) memories always get a REAL chance — reserved slots, not
    # just a fallback used when the scoped pool is completely empty. A universal tip ("always
    # sanity-check turnover before finalizing") shouldn't get crowded out just because some
    # field-scoped memory also happened to match something, however weakly.
    general_slots = max(3, limit // 2)
    added = 0
    for x in retrieve_memories(query, region=region, limit=limit):
        if x["id"] in seen: continue
        seen.add(x["id"]); items.append(x); added += 1
        if added >= general_slots: break
    if not items:
        return ""
    lines=["=== RELEVANT ACE KNOWLEDGE (advisory evidence; do not override hard rules) ==="]
    for x in items[:limit]:
        scope=" | ".join(v for v in [x.get("region"),x.get("dataset"),x.get("field"),x.get("operator")] if v)
        lines.append(f"- {x.get('title','Untitled')} [{x.get('type','tip')}; confidence={x.get('confidence','unverified')}{'; '+scope if scope else ''}]: {x.get('content','')}")
    return "\n".join(lines)


def auto_select_fields(query: str, region: str, delay: int, instrument: str = "", universe: str = "",
                       max_datasets: int | None = None, max_fields: int | None = None):
    """Score the local catalogue against a free-text idea/instruction and pick the fields that
    best fit it — no manual Data Explorer selection required. Same relevance-gated (score > 0),
    uncapped-by-default approach Autopilot uses for a list of hypotheses, just driven by one
    prompt string instead. Returns (fields, categories, dataset_names) ready to hand straight
    to run_generation()/suggest_templates().

    The scorer itself (_catalogue_score) lives in app.research.service — imported here lazily
    to avoid a module-load-order issue, since research.service already imports this module at
    call time for the same reason in the other direction (Autopilot's own field selection).
    """
    from app.research.service import _catalogue_score

    datasets = catalogue_datasets(region, delay, instrument, universe)
    if not datasets:
        return [], {}, []
    scored = [(d, _catalogue_score(query, d)) for d in datasets]
    chosen = [d for d, s in sorted(scored, key=lambda x: x[1], reverse=True) if s > 0]
    if max_datasets:
        chosen = chosen[:max_datasets]
    if not chosen:
        return [], {}, []

    ids = [d["id"] for d in chosen]
    rows = catalogue_fields(ids, region, delay)
    scored_f = [(f, _catalogue_score(query, f)) for f in rows]
    fields = [f for f, s in sorted(scored_f, key=lambda x: x[1], reverse=True) if s > 0]
    if max_fields:
        fields = fields[:max_fields]
    if not fields:
        return [], {}, []

    category_by_dataset = {d["id"]: d.get("category", "") for d in chosen}
    categories = {f["id"]: category_by_dataset.get(f.get("dataset_id"), "") for f in fields}
    used_ds_ids = {f.get("dataset_id") for f in fields}
    dataset_names = [d.get("name") or d["id"] for d in chosen if d["id"] in used_ds_ids]
    return fields, categories, dataset_names


def resolve_fields_from_text(text: str, region: str, delay: int, instrument: str = "", universe: str = ""):
    """Resolve a block of pasted datafield descriptions back to catalogued fields — one
    reference per line: a bare field id, an "id: description" or "id | description | type"
    row, or a line of free prose describing the field. Local catalogue only, never BRAIN.

    Each line is matched two ways, in priority order: (1) an exact field-id token in the line
    (split on common punctuation) — the reliable case, e.g. a literal id was pasted in; (2) a
    fallback fuzzy match, scoring the line's own text against every catalogued field's
    description/name/type the same way auto_select_fields scores a whole idea, keeping only
    the best match and only above a floor (avoids matching a vague short line to something
    unrelated just because it happened to score highest of a bad field).

    Returns (fields, categories, dataset_names, unmatched_lines) — unmatched_lines lets the
    caller show the user exactly which pasted lines didn't resolve to anything, rather than
    silently dropping them.
    """
    from app.research.service import _catalogue_score

    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if len(ln) > 1]
    if not lines:
        return [], {}, [], []

    datasets = catalogue_datasets(region, delay, instrument, universe)
    if not datasets:
        return [], {}, [], lines
    all_fields = catalogue_fields([d["id"] for d in datasets], region, delay)
    if not all_fields:
        return [], {}, [], lines
    by_id_lower = {f["id"].lower(): f for f in all_fields}
    category_by_dataset = {d["id"]: d.get("category", "") for d in datasets}

    matched: dict = {}
    unmatched: list = []
    for ln in lines:
        tokens = [t for t in re.split(r"[\s,;:|/\\()\[\]{}\"'=]+", ln) if t]
        hit = next((by_id_lower[t.lower()] for t in tokens if t.lower() in by_id_lower), None)
        if not hit:
            best, best_score = None, 0.0
            for f in all_fields:
                sc = _catalogue_score(ln, f)
                if sc > best_score:
                    best, best_score = f, sc
            if best is not None and best_score >= 0.35:
                hit = best
        if hit is not None:
            matched[hit["id"]] = hit
        else:
            unmatched.append(ln)

    fields = list(matched.values())
    categories = {f["id"]: category_by_dataset.get(f.get("dataset_id"), "") for f in fields}
    used_ds_ids = {f.get("dataset_id") for f in fields}
    dataset_names = [d.get("name") or d["id"] for d in datasets if d["id"] in used_ds_ids]
    return fields, categories, dataset_names, unmatched
