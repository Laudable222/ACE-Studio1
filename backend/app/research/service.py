"""Research engine.

An LLM research step that is deliberately SEPARATE from generation: the user researches a
category / datasets / region (optionally attaching a research paper), gets structured
hypotheses grounded in the exact fields provided, and only then pushes chosen ideas to
generation or templates. It reuses the studio's provider stack (llm_providers, keys) via
the brain adapter's sys.path, so no LLM code is duplicated.

Output is requested as a JSON array of delimited strings
    idea ||| mechanism ||| expected sign ||| horizon ||| candidate FastExpr
which `extract_list` returns cleanly, and we parse into structured hypotheses. This keeps
one LLM call and avoids relying on nested-object parsing.
"""

from __future__ import annotations

import json
import time

from app.brain import engine  # noqa: F401  — loads the vendored engine

import llm_providers as L  # noqa: E402
import keys as keymgr       # noqa: E402

DELIM = "|||"


def providers() -> dict:
    preferred = keymgr.get_preferred()
    return {"available": [p.name for p in L.all_available()],
            "preferred": preferred,
            "used": [p.name for p in L.default_chain(preferred)]}


def _field_summary(fields: list, categories: dict | None = None, max_fields: int = 40) -> str:
    categories = categories or {}
    lines = []
    for f in fields[:max_fields]:
        t = str(f.get("type", "MATRIX")).upper()
        desc = str(f.get("description", ""))[:70]
        cat = categories.get(f.get("id"), "")
        tag = ("VECTOR" if t == "VECTOR" else "MATRIX") + (("|" + cat) if cat else "")
        lines.append(f"  [{tag}] {f.get('id')}: {desc}")
    return "\n".join(lines) or "(no fields provided)"


def _generalisation_rule(categories: dict) -> str:
    cats = sorted({str(c) for c in (categories or {}).values() if c})
    if len(cats) > 1:
        return ("GENERALISE ACROSS CATEGORIES: several data categories are present (" + ", ".join(cats) +
                "). Frame each hypothesis at the CATEGORY / mechanism level and span the different "
                "categories — not a single field or a single category.\n")
    return ("GENERALISE ACROSS THE DATAFIELDS: a single category is being explored, so frame each "
            "hypothesis generally over its datafields (the shared signal they carry), not overfit to one field.\n")


_MODE_RULES = {
    "single": (
        "FIELD USE — SINGLE FIELD (default): each candidate FastExpr uses EXACTLY ONE datafield. "
        "Operators needing 2+ inputs reuse that SAME field under a DIFFERENT transformation — never a "
        "second field."),
    "multi_single_dataset": (
        "FIELD USE — MULTI-FIELD, ONE DATASET: each candidate MAY combine two or more datafields, but ALL "
        "from the SAME dataset. Combine them meaningfully (ratio, spread, ts_corr, conditioning)."),
    "multi_two_categories": (
        "FIELD USE — MULTI-FIELD, TWO CATEGORIES: each candidate MAY combine datafields from AT MOST TWO "
        "categories (each field's category is in its [TYPE|category] tag). NEVER mix three or more "
        "categories — that overfits and is rejected."),
}


def _prompt(category, region, delay, instrument, dataset_names, fields, categories, goal,
            paper_text, paper_is_community, mode, max_operators, n) -> str:
    paper_block = ""
    if paper_text:
        which = ("This is a WorldQuant COMMUNITY paper — map its mechanism ONLY onto the specific datasets/"
                 "fields the user selected for it. " if paper_is_community else
                 "This is the user's own paper. ")
        paper_block = ("\n=== RESEARCH PAPER EXCERPT ===\n" + which +
                       "Extract the underlying economic mechanism and adapt it to the datafields below. "
                       "Do NOT invent or reference any field the paper mentions but that is not in the list.\n"
                       + paper_text[:40000] + "\n")   # use the whole paper / specified pages
    goal_line = f"User goal: {goal}\n" if goal else ""
    mode_rule = _MODE_RULES.get(mode, _MODE_RULES["single"])
    from app.generation import service as gen
    from app.knowledge import categories as cats
    from app.operators import service as opsvc
    op_block = gen.operator_summary()
    op_examples = opsvc.reference_block("REGULAR")
    cat_guide = cats.combination_guidance(mode, list((categories or {}).values()))
    return (
        "You are a senior quantitative researcher designing alpha ideas for the WorldQuant BRAIN platform. "
        "Your hypotheses must be concrete, mechanism-first, and directly testable — not generic.\n"
        f"Market context: region {region}, delay {delay}, {instrument}. "
        f"Data category: {category or 'unspecified'}. Datasets: {', '.join(dataset_names) or 'unspecified'}.\n"
        "Reason explicitly about what drives THIS market and region — liquidity and microstructure, who "
        "trades and why, settlement/delay conventions, how and how fast information diffuses into prices — "
        "and about which of the fields below actually captures that force, on what horizon, and why the "
        "effect should persist rather than be arbitraged away.\n"
        + (f"NOTE: delay {delay} — for delay 0, favour ideas whose edge is a strong risk-adjusted return "
           "(Sharpe), since delay-0 alphas are judged primarily on Sharpe.\n" if str(delay) == "0" else "")
        + goal_line + paper_block +
        f"\nPropose {n} DISTINCT, testable hypotheses. Each must exploit a DIFFERENT economic mechanism AND a "
        "DIFFERENT operator family; extract signal in varied ways (arithmetic, time-series momentum/reversal/"
        "seasonality, cross-sectional rank/zscore/scale, group-relative neutralisation, logical/conditional "
        "regime gates) — never repeat one shape.\n"
        + _generalisation_rule(categories) +
        "The IDEA and MECHANISM must be GENERAL (about the data, not one specific field); the candidate "
        "expression then ILLUSTRATES it using specific field(s) from the list. Collectively COVER all the "
        "datasets/categories provided — do not fixate on one.\n"
        "=== HARD RULES FOR THE CANDIDATE EXPRESSION ===\n"
        f"- {mode_rule}\n"
        "- Use ONLY the datafields listed below. NEVER invent, rename, or assume any other field.\n"
        "- FIELD TYPE MATTERS: a [VECTOR] field MUST be wrapped in a vec_* operator (vec_avg, vec_sum, "
        "vec_max, vec_min, vec_stddev, vec_norm, …) to reduce it to a scalar BEFORE any other operator uses "
        "it — e.g. ts_zscore(vec_avg(field), 120), never ts_zscore(field, 120) for a VECTOR field. A "
        "[MATRIX] field is used directly and must NOT be wrapped in vec_*.\n"
        f"- Use AT MOST {max_operators} operators. Pass NAMED parameters as keywords (name=value); windows/"
        "groups are positional bare values; a group is an identifier (industry/subindustry/sector/market), "
        "never a number. NEVER use bucket().\n\n"
        + cat_guide + "\n"
        + f"=== OPERATORS (use ONLY these, with correct arity) ===\n{op_block}\n{op_examples}\n"
        + f"=== DATAFIELDS (type|category tagged) ===\n{_field_summary(fields, categories)}\n\n"
        + f"Return ONLY a JSON array of {n} strings. Each string MUST use this exact delimited form:\n"
        f"  idea {DELIM} mechanism (why it predicts returns, region-aware) {DELIM} expected sign {DELIM} "
        f"horizon in days {DELIM} confidence 1-5 (your conviction in the edge) {DELIM} candidate FastExpr "
        f"(following ALL rules above)\n"
    )


def _parse(items: list) -> tuple[list, list]:
    hypotheses, expressions = [], []
    for s in items:
        parts = [p.strip() for p in str(s).split(DELIM)]
        if len(parts) >= 2:
            # The expression is always the LAST field; confidence (1-5) is the 5th when present.
            conf = ""
            if len(parts) >= 6:
                conf, expr = parts[4], parts[5]
            else:
                expr = parts[4] if len(parts) > 4 else ""
            try:
                conf_n = max(1, min(5, int(float(conf))))
            except (TypeError, ValueError):
                conf_n = 0
            h = {"idea": parts[0], "mechanism": parts[1],
                 "sign": parts[2] if len(parts) > 2 else "",
                 "horizon": parts[3] if len(parts) > 3 else "",
                 "confidence": conf_n, "expression": expr}
            hypotheses.append(h)
            if h["expression"]:
                expressions.append(h["expression"])
        else:
            hypotheses.append({"idea": str(s), "mechanism": "", "sign": "", "horizon": "", "confidence": 0, "expression": ""})
    return hypotheses, expressions



def _tokens(text: str) -> set[str]:
    """Small dependency-free tokenizer used only for catalogue matching."""
    words = _re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", str(text or "").lower())
    stop = {
        "the","and","for","with","from","that","this","into","over","under","than",
        "using","use","field","data","dataset","alpha","return","returns","market",
        "price","stock","stocks","signal","signals","based","expected","should",
        "their","where","which","will","have","has","are","was","were","into",
    }
    return {w for w in words if w not in stop}


def _catalogue_score(query: str, row: dict) -> float:
    """Rank a BRAIN dataset/field against a hypothesis without inventing identifiers."""
    q = _tokens(query)
    text = " ".join(str(row.get(k, "") or "") for k in (
        "id", "name", "description", "category", "category_name", "category_id",
        "subcategory", "subcategory_name", "type"
    ))
    t = _tokens(text)
    if not q or not t:
        return 0.0
    overlap = len(q & t)
    # Give exact phrase-ish metadata a modest boost while keeping the score deterministic.
    joined = text.lower()
    phrase = sum(1 for w in q if w in joined)
    return overlap / max(1, len(q)) + 0.15 * phrase / max(1, len(q))


def _autopilot_discovery_prompt(*, region, delay, instrument, goal, paper_text, n) -> str:
    paper = ""
    if paper_text:
        paper = (
            "\n=== RESEARCH MATERIAL ===\n"
            "Extract economic mechanisms and testable hypotheses from this material. "
            "Do not assume any particular BRAIN field exists yet.\n" + paper_text[:40000] + "\n"
        )
    return (
        "You are the hypothesis-discovery stage of an autonomous quantitative research engine "
        "for WorldQuant BRAIN. Discover concrete, region-aware economic hypotheses BEFORE choosing "
        "datafields. Do not invent field identifiers. Each hypothesis must be testable and use a "
        "different economic mechanism. Return ONLY a JSON array of strings in this exact form:\n"
        f"idea {DELIM} mechanism {DELIM} expected sign {DELIM} horizon in days {DELIM} confidence 1-5\n"
        f"Generate exactly {n} hypotheses.\n"
        f"Region: {region}. Delay: {delay}. Instrument: {instrument}.\n"
        + (f"Research goal: {goal}\n" if goal else "")
        + paper
    )


def _autopilot_field_selection(*, hypotheses, datasets, region, delay, instrument,
                               max_datasets_per_hypothesis=6, max_fields_per_hypothesis=16,
                               progress=None):
    """
    Build a hypothesis -> verified BRAIN datasets/fields map.

    All datasets returned by BRAIN are scored. Only the best few datasets per hypothesis
    are expanded into fields, which keeps the catalogue scan broad without downloading an
    impractical number of field rows.
    """
    from app.brain import engine as brain

    ranked = []
    for h in hypotheses:
        q = f"{h.get('idea','')} {h.get('mechanism','')}"
        ds = sorted(datasets, key=lambda d: _catalogue_score(q, d), reverse=True)
        chosen = [d for d in ds[:max_datasets_per_hypothesis] if d.get("id")]
        ranked.append({"hypothesis": h, "datasets": chosen})

    unique_ids = list(dict.fromkeys(
        str(d.get("id")) for r in ranked for d in r["datasets"] if d.get("id")
    ))
    if progress:
        progress(message=f"catalogue scan: {len(datasets)} datasets → {len(unique_ids)} matched datasets")

    if not unique_ids:
        return ranked

    eff_region, eff_delay, eff_universe = brain.valid_combo(instrument, region, delay, "TOP3000")
    df, _ = brain.fetch_fields(unique_ids, eff_region, eff_universe, eff_delay, instrument, "ALL", "")
    rows = []
    if df is not None and not getattr(df, "empty", True):
        rows = brain._json_safe(df.to_dict(orient="records"))

    for r in ranked:
        q = f"{r['hypothesis'].get('idea','')} {r['hypothesis'].get('mechanism','')}"
        dsids = {d.get("id") for d in r["datasets"]}
        fs = [f for f in rows if f.get("dataset_id") in dsids]
        fs.sort(key=lambda f: _catalogue_score(q, f), reverse=True)
        r["fields"] = fs[:max_fields_per_hypothesis]
        for f in r["fields"]:
            f["catalogue_score"] = round(_catalogue_score(q, f), 4)
    return ranked


def _autopilot_generation_prompt(*, region, delay, instrument, goal, paper_text,
                                 ranked, max_operators) -> str:
    # One compact prompt contains the hypothesis-to-fields mapping. This makes the LLM
    # choose only verified identifiers and keeps one generated expression attached to each idea.
    from app.generation import service as gen
    from app.operators import service as opsvc
    from app.knowledge import categories as cats

    all_fields = []
    cats_map = {}
    ds_names = []
    blocks = []
    for i, r in enumerate(ranked, 1):
        h = r["hypothesis"]
        for f in r.get("fields", []):
            if f.get("id") and f.get("id") not in {x.get("id") for x in all_fields}:
                all_fields.append(f)
            if f.get("id"):
                cats_map[f["id"]] = f.get("category_id") or f.get("category_name") or ""
        ds_names.extend(str(d.get("name") or d.get("id") or "") for d in r.get("datasets", []))
        fl = ", ".join(str(f.get("id")) for f in r.get("fields", []) if f.get("id"))
        blocks.append(
            f"HYPOTHESIS {i}: {h.get('idea','')} | {h.get('mechanism','')} | "
            f"sign={h.get('sign','')} | horizon={h.get('horizon','')}\n"
            f"VERIFIED FIELDS FOR THIS HYPOTHESIS: {fl or '(none)'}"
        )

    if not all_fields:
        return ""
    return (
        "You are the expression-generation stage of an autonomous WorldQuant BRAIN research engine.\n"
        f"Region: {region}. Delay: {delay}. Instrument: {instrument}. "
        f"Goal: {goal or 'test the economic hypotheses faithfully'}.\n"
        "For EACH hypothesis, generate exactly one candidate FastExpr using ONLY fields listed "
        "for that hypothesis. Never invent or borrow a field from another hypothesis. The expression "
        "must test the mechanism, not merely mention it. Use a different operator family across hypotheses "
        f"where practical and use at most {max_operators} operators.\n"
        "VECTOR fields must first be reduced with an appropriate vec_* operator. MATRIX fields are used directly. "
        "Use only valid operators and signatures from the catalogue below.\n\n"
        "=== HYPOTHESES AND THEIR VERIFIED DATA ===\n" + "\n\n".join(blocks) +
        f"\n\n=== OPERATOR CATALOGUE ===\n{gen.operator_summary()}\n{opsvc.reference_block('REGULAR')}\n"
        f"\n=== ALL VERIFIED FIELDS ===\n{_field_summary(all_fields, cats_map, max_fields=120)}\n\n"
        f"Return ONLY a JSON array of exactly {len(ranked)} strings, one per hypothesis, in this form:\n"
        f"idea {DELIM} mechanism {DELIM} expected sign {DELIM} horizon {DELIM} confidence 1-5 {DELIM} FastExpr\n"
    )


def run_autopilot(*, category, region, delay, instrument, goal, paper_text, paper_name,
                   n, max_operators, progress) -> dict:
    """
    Autonomous path:
      hypothesis discovery -> complete BRAIN dataset scan -> per-hypothesis dataset/field ranking
      -> expression generation -> strict validation.

    This deliberately leaves the existing manual Research Lab path untouched.
    """
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("research")
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")

    from app.brain import engine as brain
    from app.core.llm_router import TaskLLM

    progress(message=f"autopilot: discovering {n} hypotheses with {', '.join(p.name for p in chain)}…")
    discovery = TaskLLM("research").generate_list(
        _autopilot_discovery_prompt(region=region, delay=delay, instrument=instrument, goal=goal,
                                    paper_text=paper_text, n=n), n=n)
    hypotheses, _ = _parse(discovery.expressions)
    hypotheses = hypotheses[:n]
    if not hypotheses:
        raise RuntimeError("Research produced no hypotheses. The report could not be converted into testable mechanisms.")

    progress(message=f"autopilot: scanning BRAIN datasets for {len(hypotheses)} hypotheses…")
    eff_region, eff_delay, eff_universe = brain.valid_combo(instrument, region, delay, "TOP3000")
    datasets = brain.get_datasets(eff_region, eff_universe, eff_delay, instrument)
    datasets = brain._json_safe(datasets or [])
    ranked = _autopilot_field_selection(
        hypotheses=hypotheses, datasets=datasets, region=eff_region, delay=eff_delay,
        instrument=instrument, progress=progress
    )

    total_fields = sum(len(r.get("fields", [])) for r in ranked)
    if not total_fields:
        raise RuntimeError(
            f"BRAIN returned {len(datasets)} datasets, but no verified fields matched the hypotheses "
            f"for {eff_region}/D{eff_delay}."
        )

    progress(message=f"autopilot: {total_fields} verified hypothesis-specific fields selected; generating expressions…")
    meta = _autopilot_generation_prompt(
        region=eff_region, delay=eff_delay, instrument=instrument, goal=goal,
        paper_text=paper_text, ranked=ranked, max_operators=max_operators
    )
    if not meta:
        raise RuntimeError("No verified fields were available for expression generation.")

    res = TaskLLM("alpha_generation").generate_list(meta, n=len(ranked))
    generated, _ = _parse(res.expressions)

    # Attach generated candidates in order. If the provider returned fewer items, retain
    # the hypothesis and mark the missing expression rather than silently shifting mappings.
    for i, r in enumerate(ranked):
        h = r["hypothesis"]
        g = generated[i] if i < len(generated) else {}
        h.update({k: g.get(k, h.get(k, "")) for k in ("mechanism", "sign", "horizon", "confidence", "expression")})
        h["datasets"] = [
            {"id": d.get("id"), "name": d.get("name"), "score": round(_catalogue_score(
                f"{h.get('idea','')} {h.get('mechanism','')}", d), 4)}
            for d in r.get("datasets", [])
        ]
        h["fields"] = [
            {"id": f.get("id"), "dataset_id": f.get("dataset_id"),
             "type": f.get("type", "MATRIX"), "score": f.get("catalogue_score", 0)}
            for f in r.get("fields", [])
        ]

    expressions = []
    for r in ranked:
        h = r["hypothesis"]
        e = (h.get("expression") or "").strip()
        allowed = r.get("fields", [])
        if not e:
            h["expression_valid"] = False
            h["expression_issues"] = ["NO_EXPRESSION"]
            continue
        from app.generation import service as gen
        val = gen._validator(allowed, max_operators=max_operators, multi_field=True)
        vr = val.validate(e)
        h["expression_valid"] = bool(vr.ok)
        if not vr.ok:
            h["expression_issues"] = [x.code for x in vr.issues]
        else:
            expressions.append(e)

    progress(message=f"autopilot: {len(expressions)} valid expressions from {len(hypotheses)} hypotheses")
    return {
        "hypotheses": [r["hypothesis"] for r in ranked],
        "expressions": expressions,
        "provider": res.provider,
        "model": res.model,
        "region": eff_region,
        "delay": eff_delay,
        "universe": eff_universe,
        "datasets_scanned": len(datasets),
        "matched_datasets": len({d["id"] for r in ranked for d in r.get("datasets", []) if d.get("id")}),
        "fields_matched": total_fields,
    }


def run_research(*, category, region, delay, instrument, dataset_names, fields, categories, goal,
                 paper_text, paper_is_community, mode, max_operators, n, progress) -> dict:
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("research")
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings "
                           "(Claude, Gemini, OpenAI, DeepSeek, Groq, or Hugging Face).")
    progress(message=f"researching with {', '.join(p.name for p in chain)}…")
    meta = _prompt(category, region, delay, instrument, dataset_names, fields, categories, goal,
                   paper_text, paper_is_community, mode, max_operators, n)
    from app.knowledge.service import memory_prompt_context
    mem = memory_prompt_context((goal or "") + " " + (paper_text or ""), region=region, fields=fields, datasets=dataset_names, limit=8)
    if mem:
        meta = meta + "\n\n" + mem
    res = __import__("app.core.llm_router", fromlist=["TaskLLM"]).TaskLLM("research").generate_list(meta, n=n)
    hypotheses, _ = _parse(res.expressions)

    # Validate every candidate against the EXACT fields provided (types + vector wrapping) so a
    # hypothesis can never carry an invented field or an unwrapped VECTOR into generation/simulation.
    from app.generation import service as gen
    multi = mode != "single"
    val = gen._validator(fields, max_operators=max_operators, multi_field=multi)
    field_ids = {f["id"] for f in fields}
    two_cat = mode == "multi_two_categories" and bool(categories)

    expressions = []
    for h in hypotheses:
        e = (h.get("expression") or "").strip()
        if not e:
            h["expression_valid"] = False
            continue
        r = val.validate(e)
        ok = r.ok
        issues = [i.code for i in r.issues]
        if ok and two_cat:
            used = {categories.get(fid) for fid in gen._fields_in(e, field_ids) if categories.get(fid)}
            if len(used) > 2:
                ok = False
                issues.append("MULTI_CATEGORY")
        h["expression_valid"] = ok
        if not ok:
            h["expression_issues"] = issues
        else:
            expressions.append(e)

    progress(message=f"{len(expressions)} valid of {len(hypotheses)} hypotheses via {res.provider}")
    return {"hypotheses": hypotheses, "expressions": expressions,
            "provider": res.provider, "model": res.model}


# ── persistence ──────────────────────────────────────────────────────────────────────

def save_session(payload: dict) -> int:
    from app.db.base import SessionLocal
    from app.db import models as M
    with SessionLocal() as db:
        row = M.ResearchSession(
            category=payload.get("category", ""), region=payload.get("region", ""),
            delay=int(payload.get("delay", 1)), goal=payload.get("goal", ""),
            datasets_json=json.dumps(payload.get("dataset_names", [])),
            fields_json=json.dumps([f.get("id") for f in payload.get("fields", [])]),
            provider=payload.get("provider", ""), model=payload.get("model", ""),
            paper_name=payload.get("paper_name", ""),
            hypotheses_json=json.dumps(payload.get("hypotheses", [])),
            expressions_json=json.dumps(payload.get("expressions", [])),
            templates_json=json.dumps(payload.get("templates", [])))
        db.add(row)
        db.commit()
        return row.id


def list_sessions(limit: int = 30) -> list:
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    with SessionLocal() as db:
        rows = db.scalars(select(M.ResearchSession).order_by(M.ResearchSession.created_at.desc()).limit(limit)).all()
        out = []
        for r in rows:
            out.append({"id": r.id, "created_at": r.created_at, "category": r.category,
                        "region": r.region, "delay": r.delay, "goal": r.goal,
                        "provider": r.provider, "paper_name": r.paper_name,
                        "hypotheses": json.loads(r.hypotheses_json or "[]"),
                        "expressions": json.loads(r.expressions_json or "[]")})
        return out


import re as _re


def _normalize(text: str) -> str:
    return _re.sub(r"\s+", " ", (text or "").strip().lower())


def _fallback_name(category: str, region: str, source: str) -> str:
    base = (category or "Research").replace(",", " /").strip().title()
    tag = "Strategy" if source == "strategy" else "Research"
    return f"{base} {tag} · {region}"


def _fallback_compose(raw: str, category: str, region: str, dataset_names, source: str) -> dict:
    ds = ", ".join(dataset_names or []) or "the selected datasets"
    body = (
        "## Role\n"
        "You are a senior quantitative researcher and WorldQuant BRAIN alpha engineer, working with a "
        "risk-aware portfolio manager's mindset.\n\n"
        "## Context\n"
        f"Region {region}. Category: {category or 'mixed'}. Datasets: {ds}.\n\n"
        "## Objective\n"
        "Generate high-quality, diverse alpha expressions that TEST the researched ideas below and add "
        "genuine, low-correlation value.\n\n"
        "## Instructions\n" + (raw or "").strip() + "\n\n"
        "## Constraints\n"
        "Use ONLY the provided datafields; wrap VECTOR fields in a vec_* operator before use; pass named "
        "parameters as keywords; every expression must express a DIFFERENT mechanism; treat any example "
        "expression as an illustration, not a field restriction."
    )
    return {"name": _fallback_name(category, region, source), "body": body}


def nice_name(raw, category, region, source="research") -> str:
    """Just a concise, descriptive name for a saved prompt (no body rewrite). LLM name-only with a
    deterministic fallback."""
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("research")
    if not chain:
        return _fallback_name(category, region, source)
    meta = ("Return ONLY a JSON array containing exactly ONE string: a concise, descriptive 4-8 word name "
            "(human readable, NOT just the category) for a prompt that generates WorldQuant BRAIN alpha "
            "expressions from the notes below.\n"
            f"Region {region}. Category: {category or 'mixed'}.\n=== NOTES ===\n{(raw or '')[:1500]}\n")
    try:
        from app.core import llm_cache
        res = llm_cache.cached_generate_list("research", meta, n=1)
        items = [str(x).strip().strip('"') for x in res.expressions if str(x).strip()]
        return items[0][:80] if items else _fallback_name(category, region, source)
    except Exception:
        return _fallback_name(category, region, source)


def compose_prompt(*, raw, category, region, dataset_names, source="research") -> dict:
    """Wrap raw research/strategy notes in a STRUCTURED prompt (Role → Context → Objective →
    Instructions → Constraints) with a concise, descriptive name. The LLM only writes the framing
    sections and the name; the ORIGINAL notes are embedded VERBATIM as the Instructions section,
    so every hypothesis/strategy survives intact. Falls back fully if no LLM is available."""
    raw = (raw or "").strip()
    ds_line = ", ".join(dataset_names or []) or "the selected datasets"
    instructions = (
        "## Instructions\n"
        "Generate diverse alpha expressions that TEST each of the ideas below. Treat every idea on its own; "
        "spread across the ideas and the available datafields. Any example expression is an ILLUSTRATION of the "
        "mechanism, NOT a field restriction.\n\n" + raw
    )
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("research")
    if not chain:
        return _fallback_compose(raw, category, region, dataset_names, source)
    meta = (
        "You are packaging research notes into a high-quality prompt for generating WorldQuant BRAIN alphas. Do "
        "NOT rewrite or summarise the notes themselves — only write the framing.\n"
        "Return ONLY a JSON array of strings:\n"
        "- FIRST string: 'NAME||| <a concise, descriptive 4-8 word name, human readable, NOT just the category>'.\n"
        "- Then ONE string per section, each starting with a '## ' header, in this order: '## Role' (assign one or "
        "MORE expert roles), '## Context' (region, category, datasets), '## Objective', '## Constraints' (use only "
        "provided datafields, wrap VECTOR fields in a vec_* op, pass named params as keywords, every expression a "
        "DIFFERENT mechanism, examples are illustrative). Do NOT include an Instructions section — it is added "
        "separately.\n"
        f"Region: {region}. Category: {category or 'mixed'}. Datasets: {ds_line}.\n"
        f"For reference only (do not copy): the notes have {raw.count(chr(10)) + 1} lines of ideas.\n")
    try:
        res = __import__("app.core.llm_router", fromlist=["TaskLLM"]).TaskLLM("research").generate_list(meta, n=6)
        items = [str(x).strip() for x in res.expressions if str(x).strip()]
        name, role, context, objective, constraints = "", "", "", "", ""
        for it in items:
            up = it.upper()
            if up.startswith("NAME|||") or up.startswith("NAME:") or up.startswith("NAME "):
                name = _re.split(r"\|\|\||:", it, maxsplit=1)[-1].strip().strip('"')[:80]
            elif up.startswith("## ROLE"):
                role = it
            elif up.startswith("## CONTEXT"):
                context = it
            elif up.startswith("## OBJECTIVE"):
                objective = it
            elif up.startswith("## CONSTRAINT"):
                constraints = it
        if not (role or context or objective):
            return _fallback_compose(raw, category, region, dataset_names, source)
        role = role or "## Role\nYou are a senior quantitative researcher and WorldQuant BRAIN alpha engineer."
        context = context or f"## Context\nRegion {region}. Category: {category or 'mixed'}. Datasets: {ds_line}."
        objective = objective or "## Objective\nGenerate high-quality, diverse alphas that add low-correlation value."
        constraints = constraints or (
            "## Constraints\nUse ONLY the provided datafields; wrap VECTOR fields in a vec_* operator; pass named "
            "parameters as keywords; every expression must express a DIFFERENT mechanism.")
        body = "\n\n".join([role, context, objective, instructions, constraints])
        if not name:
            name = _fallback_name(category, region, source)
        return {"name": name, "body": body, "provider": res.provider}
    except Exception:
        return _fallback_compose(raw, category, region, dataset_names, source)


def save_prompt(*, name, scope, category, region, body, dataset_names, research_id=0) -> dict:
    """Save a prompt, avoiding duplicates: if an existing prompt in the same scope has the same
    (normalised) body, return it instead of creating another."""
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    norm = _normalize(body)
    with SessionLocal() as db:
        for e in db.scalars(select(M.Prompt).where(M.Prompt.scope == scope)).all():
            if _normalize(e.body) == norm:
                return {"id": e.id, "name": e.name, "duplicate": True}
        row = M.Prompt(name=name, scope=scope, category=category, region=region, body=body,
                       datasets_json=json.dumps(dataset_names or []), source_research_id=research_id)
        db.add(row)
        db.commit()
        return {"id": row.id, "name": row.name, "duplicate": False}


def export_all() -> dict:
    """Everything portable: saved prompts (generate + template scope) and research sessions."""
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    with SessionLocal() as db:
        prompts = [{"name": p.name, "scope": p.scope, "category": p.category, "region": p.region,
                    "body": p.body, "datasets": json.loads(p.datasets_json or "[]")}
                   for p in db.scalars(select(M.Prompt)).all()]
        sessions = [{"category": s.category, "region": s.region, "delay": s.delay, "goal": s.goal,
                     "dataset_names": json.loads(s.datasets_json or "[]"), "provider": s.provider,
                     "paper_name": s.paper_name, "hypotheses": json.loads(s.hypotheses_json or "[]"),
                     "expressions": json.loads(s.expressions_json or "[]")}
                    for s in db.scalars(select(M.ResearchSession)).all()]
    return {"format": "ace-studio/v2", "exported_at": time.time(), "prompts": prompts, "research_sessions": sessions}


def import_all(payload: dict) -> dict:
    """Merge an exported bundle back in, de-duplicating prompts by body."""
    added_p = 0
    for p in payload.get("prompts", []):
        r = save_prompt(name=p.get("name", ""), scope=p.get("scope", "generate"),
                        category=p.get("category", ""), region=p.get("region", ""),
                        body=p.get("body", ""), dataset_names=p.get("datasets", []))
        if not r.get("duplicate"):
            added_p += 1
    added_s = 0
    for s in payload.get("research_sessions", []):
        try:
            save_session(s); added_s += 1
        except Exception:
            pass
    return {"prompts_added": added_p, "sessions_added": added_s}


def extract_pdf_text(data: bytes, pages: str = "") -> str:
    """Extract text from a PDF. `pages` is a 1-based spec like '1-3,5' (empty = whole doc)."""
    import io
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    total = len(reader.pages)
    want = _page_set(pages, total)
    chunks = []
    for i in range(total):
        if want is None or (i + 1) in want:
            try:
                chunks.append(reader.pages[i].extract_text() or "")
            except Exception:
                pass
    return "\n".join(chunks).strip()


def _page_set(spec: str, total: int):
    spec = (spec or "").strip()
    if not spec:
        return None
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                out.update(range(int(a), int(b) + 1))
            except ValueError:
                pass
        elif part.isdigit():
            out.add(int(part))
    return {p for p in out if 1 <= p <= total} or None
