"""Generation engine (v2).

Two grounded modes, both LLM-driven and both validated/repaired before anything is shown:

  single  — DEEP signal extraction from ONE field per expression, covering every structural
            mechanism (arithmetic, time-series, cross-sectional, group-relative, logical),
            not just a couple of operators.
  multi   — combine fields, but STRICTLY from at most TWO data categories. Enforced in the
            prompt AND by a hard post-validation reject, so a 3+-category expression never
            slips through either path.

A diversity engine reads the local usage table to steer each user's generation toward
under-used operators and fields (and records what this batch used), so two users — and the
same user over time — diverge rather than converging on a few favourites.

Reuses the studio engine (validator, llm_providers, wqb_llm, keys) via the brain adapter.
"""

from __future__ import annotations

import time

from app.brain import engine  # loads the vendored engine + gives cached operators

import validator as V      # noqa: E402
import llm_providers as L  # noqa: E402
import wqb_llm             # noqa: E402
import keys as keymgr      # noqa: E402

from app.db.base import SessionLocal
from app.db import models as M
from sqlalchemy import select


# ── registry / validator ─────────────────────────────────────────────────────────────

def _registry(scope: str = "REGULAR") -> V.OperatorRegistry:
    ops = engine.operators_df()
    df = ops[ops["scope"] == scope] if ("scope" in ops.columns and (ops["scope"] == scope).any()) else ops
    return V.OperatorRegistry.from_dataframe(df)


def _clamp_ops(n) -> int:
    try:
        return max(1, min(20, int(n)))
    except (TypeError, ValueError):
        return 3


def _validator(fields: list, max_operators: int, multi_field: bool) -> V.Validator:
    ftypes = {f["id"]: str(f.get("type", "MATRIX")).upper() for f in fields}
    # ALWAYS restrict to the provided fields — in both single AND multi mode — so an LLM can
    # never introduce a datafield it wasn't given (UNKNOWN_FIELD then rejects it). Multi mode
    # only relaxes the single-field rule, never the which-fields-are-allowed rule.
    known = {f["id"] for f in fields}
    return V.Validator(
        _registry("REGULAR"), known_fields=known, field_types=ftypes,
        check_unknown_kwargs=True, require_single_field=not multi_field,
        max_operators=_clamp_ops(max_operators),
        op_count_ignore_prefixes=("vec_",))


# ── AST helpers (extract the raw fields an expression uses) ───────────────────────────

def _fields_in(expr: str, field_ids: set) -> list:
    try:
        ast = V.parse(expr)
    except Exception:
        return []
    out, stack = [], [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, V.Ident):
            if n.name in field_ids:
                out.append(n.name)
        elif isinstance(n, V.Call):
            stack.extend(n.args)
        elif isinstance(n, V.KwArg):
            stack.append(n.value)
        elif isinstance(n, V.Unary):
            stack.append(n.operand)
        elif isinstance(n, V.BinOp):
            stack.extend([n.left, n.right])
        elif isinstance(n, V.Seq):
            stack.extend(n.statements)
    return list(dict.fromkeys(out))


def _leaf_idents(expr: str) -> list:
    """Every identifier LEAF in the expression (datafields + group fields), regardless of a known
    field set — operators are Call.name so they're excluded, and kwarg keywords aren't values.
    Used by the cross-region pre-flight to know which datafields must exist in a region."""
    try:
        ast = V.parse(expr)
    except Exception:
        return []
    out, stack = [], [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, V.Ident):
            out.append(n.name)
        elif isinstance(n, V.Call):
            stack.extend(n.args)
        elif isinstance(n, V.KwArg):
            stack.append(n.value)
        elif isinstance(n, V.Unary):
            stack.append(n.operand)
        elif isinstance(n, V.BinOp):
            stack.extend([n.left, n.right])
        elif isinstance(n, V.Seq):
            stack.extend(n.statements)
    return list(dict.fromkeys(out))


def _operators_in(expr: str) -> list:
    try:
        ast = V.parse(expr)
    except Exception:
        return []
    out, stack = [], [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, V.Call):
            out.append(n.name)
            stack.extend(n.args)
        elif isinstance(n, V.KwArg):
            stack.append(n.value)
        elif isinstance(n, V.Unary):
            stack.append(n.operand)
        elif isinstance(n, V.BinOp):
            stack.extend([n.left, n.right])
        elif isinstance(n, V.Seq):
            stack.extend(n.statements)
    return list(dict.fromkeys(out))


# ── operator keyword / semantic enforcement (driven by the Operator Lab metadata) ────

_GROUP_IDENTS = {"industry", "subindustry", "sector", "market", "exchange", "country", "currency", "sedol"}


def _same_ast(a, b) -> bool:
    # Dataclass AST nodes have value-based repr, so equal structure => equal repr. Safe: only
    # ever flags when the two subtrees are genuinely identical.
    return repr(a) == repr(b)


def operator_issues(expr: str, specs: dict) -> list:
    """Metadata-driven checks the vendored validator doesn't do: keyword params must be passed
    by keyword (not positionally), plus the semantic rules for bucket/densify/ts_regression.
    Returns a list of (code, message)."""
    try:
        ast = V.parse(expr)
    except Exception:
        return []
    issues, stack = [], [(ast, None)]
    while stack:
        node, parent = stack.pop()
        if isinstance(node, V.Call):
            nm = node.name
            pos_args = [a for a in node.args if not isinstance(a, V.KwArg)]
            kw_args = [a for a in node.args if isinstance(a, V.KwArg)]
            spec = specs.get(nm)
            if spec:
                if len(pos_args) > spec["positional"]:
                    issues.append(("KEYWORD_REQUIRED",
                                   f"{nm}: pass its keyword parameter(s) by name (e.g. {sorted(spec['keywords'])[0]}=…), "
                                   "not positionally"))
                for kw in kw_args:
                    if getattr(kw, "name", None) and kw.name not in spec["keywords"]:
                        issues.append(("UNKNOWN_KWARG", f"{nm}: '{kw.name}=' is not a parameter of this operator"))
            low = nm.lower()
            if low == "densify" and pos_args:
                a = pos_args[0]
                ok = (isinstance(a, V.Call) and a.name.lower() == "bucket") or \
                     (isinstance(a, V.Ident) and a.name.lower() in _GROUP_IDENTS)
                if not ok:
                    issues.append(("DENSIFY_NEEDS_GROUP", "densify() must wrap a group (a bucket(...) or a group identifier)"))
            if low == "bucket":
                pl = parent.name.lower() if isinstance(parent, V.Call) else ""
                if not (pl.startswith("group_") or pl == "densify"):
                    issues.append(("BUCKET_NOT_GROUP", "bucket() builds a group — use it only as the group argument of a group_* operator (or inside densify)"))
            if low == "ts_regression" and len(pos_args) >= 2 and _same_ast(pos_args[0], pos_args[1]):
                issues.append(("TS_REGRESSION_XY_SAME", "ts_regression: x must be a transform of / different from y, not identical"))
            for a in node.args:
                stack.append((a, node))
        elif isinstance(node, V.KwArg):
            stack.append((node.value, parent))
        elif isinstance(node, V.Unary):
            stack.append((node.operand, parent))
        elif isinstance(node, V.BinOp):
            stack.append((node.left, parent)); stack.append((node.right, parent))
        elif isinstance(node, V.Seq):
            for s in node.statements:
                stack.append((s, parent))
    # de-dup preserving order
    seen, out = set(), []
    for code, msg in issues:
        if (code, msg) not in seen:
            seen.add((code, msg)); out.append((code, msg))
    return out


def _param_specs() -> dict:
    try:
        from app.operators import service as opsvc
        return opsvc.param_spec_map()
    except Exception:
        return {}


# ── diversity engine ─────────────────────────────────────────────────────────────────

def usage_snapshot(region: str) -> dict:
    with SessionLocal() as db:
        rows = db.scalars(select(M.Usage).where(M.Usage.region == region)).all()
        ops = {r.key: r.count for r in rows if r.kind == "operator"}
        fields = {r.key: r.count for r in rows if r.kind == "field"}
    return {"operators": ops, "fields": fields}


def _diversity_hint(region: str, available_ops: list) -> str:
    snap = usage_snapshot(region)
    used = snap["operators"]
    if not used:
        return ""
    over = sorted(used, key=lambda k: -used[k])[:8]
    under = [o for o in available_ops if o not in used][:14]
    parts = []
    if over:
        parts.append("You have leaned on these operators a lot already — use them SPARINGLY now: "
                     + ", ".join(over) + ".")
    if under:
        parts.append("PREFER these operators you have barely used, to widen your coverage: "
                     + ", ".join(under) + ".")
    return ("\n=== DIVERSITY (personal to you) ===\n" + " ".join(parts) + "\n") if parts else ""


def record_usage(region: str, operators: list, fields: list) -> None:
    """Credit this batch so the diversity engine keeps pushing the user outward. Called at
    generation time as a proxy for 'explored'; simulation results will later refine it."""
    now = time.time()
    with SessionLocal() as db:
        for kind, keys in (("operator", operators), ("field", fields)):
            for k in keys:
                row = db.scalar(select(M.Usage).where(
                    M.Usage.kind == kind, M.Usage.key == k, M.Usage.region == region))
                if row:
                    row.count += 1
                    row.last_at = now
                else:
                    db.add(M.Usage(kind=kind, key=k, region=region, count=1, last_at=now))
        db.commit()


# ── prompts ──────────────────────────────────────────────────────────────────────────

def _field_summary(fields: list, categories: dict, max_fields: int = 80) -> str:
    lines = []
    for f in fields[:max_fields]:
        t = str(f.get("type", "MATRIX")).upper()
        desc = str(f.get("description", ""))[:70]
        cat = categories.get(f["id"], "")
        ds = f.get("dataset_id", "")
        tag = f"[{'VECTOR' if t == 'VECTOR' else 'MATRIX'}{('|' + cat) if cat else ''}{('|ds:' + ds) if ds else ''}]"
        lines.append(f"  {tag} {f['id']}: {desc}")
    return "\n".join(lines) or "(no fields)"


def _coverage_rule(fields: list, n: int) -> str:
    """Force the batch to span EVERY selected dataset, not fixate on one."""
    dsets = list(dict.fromkeys(f.get("dataset_id") for f in fields if f.get("dataset_id")))
    if len(dsets) <= 1:
        return ""
    return ("- COVER ALL DATASETS: the selected fields span these datasets — " + ", ".join(dsets) + ". "
            f"Distribute the {n} expressions so EVERY dataset contributes (roughly evenly); do NOT draw them all "
            "from a single dataset. In single-field mode, use fields from different datasets across the batch.\n")


_SINGLE_RULES = (
    "- EXACTLY ONE raw datafield per expression (mandatory). Operators needing 2+ inputs must\n"
    "  reuse the SAME field with a DIFFERENT transformation, never two different fields.\n"
    "- Extract signal from that one field in as MANY structural ways as possible across the\n"
    "  batch: arithmetic (differences, ratios, spreads of its own transforms), time-series\n"
    "  (momentum, reversal, decay, volatility, seasonality), cross-sectional (rank, zscore,\n"
    "  scale, winsorize, normalize), group-relative (industry/sector neutralise & compare),\n"
    "  and logical/conditional (regime gates, if_else, sign, clamp). Do NOT repeat one shape.\n"
)
_MULTI_RULES = (
    "- You MAY combine two or more datafields, but STRICTLY from AT MOST TWO data categories\n"
    "  (each field's category is shown in its tag as [TYPE|category]). NEVER mix three or more\n"
    "  categories — such expressions are rejected. Combining more than two categories overfits.\n"
    "- Combine them meaningfully: correlation, ratio, spread, conditioning — e.g.\n"
    "  ts_corr(a, b, 60), subtract(rank(a), rank(b)).\n"
)
_COMMON_RULES = (
    "- Use each operator STRICTLY per its signature; windows/lookbacks and groups are POSITIONAL\n"
    "  bare values: ts_rank(x, 20), group_zscore(x, industry). A group is one of industry,\n"
    "  subindustry, sector, market, exchange, currency — NEVER a number.\n"
    "- Use ONLY the datafields listed below — NEVER invent, rename, or assume any other field.\n"
    "- FIELD TYPE MATTERS. A field tagged [VECTOR] holds many values per instrument and MUST be\n"
    "  reduced to a scalar by wrapping it in a vec_* operator (vec_avg, vec_sum, vec_max, vec_min,\n"
    "  vec_stddev, vec_norm, …) BEFORE any other operator touches it — e.g. ts_rank(vec_avg(f), 60),\n"
    "  never ts_rank(f, 60) for a VECTOR f. A field tagged [MATRIX] is used directly and must NOT\n"
    "  be wrapped in vec_*. NEVER use the bucket() operator.\n"
    "- DIVERSIFY: every expression must use a DIFFERENT core operator/structure and express a\n"
    "  DIFFERENT economic idea. Spread across ALL the datafields provided — use as many\n"
    "  different fields as there are expressions; if several datasets are present, each must\n"
    "  contribute.\n"
    "- If the instruction includes example expressions, treat them ONLY as ILLUSTRATIONS of the\n"
    "  idea/mechanism — NOT as a restriction on which fields to use. Pick the best datafields for\n"
    "  each hypothesis from the list below; your job is to TEST the hypotheses, not copy examples.\n"
    "- ARGUMENTS: pass any NAMED parameter as a KEYWORD (name=value), never positionally — e.g.\n"
    "  keep(x, f, period=5), ts_regression(y, x, 120, lag=0, rettype=0). Only a plain window/lookback or\n"
    "  a group that the signature lists as positional stays positional. Do not drop a required keyword.\n"
    "- ts_regression(y, x, d, …): x MUST be a DIFFERENT signal from y — a TRANSFORM of the field (or\n"
    "  another field). Never make x identical to y (that regression is degenerate). Options like lag/\n"
    "  rettype are keywords.\n"
    "- densify() applies ONLY to a GROUP (e.g. a bucket()/group identifier), never to a raw datafield.\n"
    "- bucket() BUILDS a group; it is NEVER a standalone alpha expression. Use its output only as the\n"
    "  GROUP argument of a group_* operator, e.g. group_zscore(x, bucket(rank(y), range=\"0,1,0.1\")).\n"
)


def build_prompt(*, mode, prompt, region, delay, instrument, universe, dataset_names,
                 fields, categories, max_operators, n, region_note) -> str:
    ops = engine.operators_df()
    ops = ops[ops["scope"] == "REGULAR"] if ("scope" in ops.columns and (ops["scope"] == "REGULAR").any()) else ops
    op_summary = wqb_llm._build_operator_summary(ops)
    op_names = [str(x) for x in (ops["name"].tolist() if "name" in ops.columns else [])]
    head = (f"Generate EXACTLY {n} distinct WorldQuant FastExpr alpha expressions.\n"
            f"Market: region {region}, universe {universe}, delay {delay}, {instrument}. "
            f"Datasets: {', '.join(dataset_names) or 'unspecified'}.\n")
    if str(delay) == "0":
        head += ("This is DELAY 0 — favour ideas with a strong risk-adjusted return (Sharpe), "
                 "since delay-0 alphas are judged primarily on Sharpe.\n")
    if region_note:
        head += region_note + "\n"
    task = (prompt + "\n\n") if prompt else ""
    rules = ("=== RULES (invalid expressions are discarded) ===\n"
             + f"- Output EXACTLY {n} expressions.\n"
             + f"- Use AT MOST {_clamp_ops(max_operators)} operators per expression.\n"
             + (_SINGLE_RULES if mode == "single" else _MULTI_RULES)
             + _COMMON_RULES
             + _coverage_rule(fields, n)
             + "Return ONLY a JSON array of expression strings.")
    div = _diversity_hint(region, op_names)
    guide = ""
    if mode == "multi":
        from app.knowledge import categories as cats
        guide = cats.combination_guidance("multi_two_categories", list((categories or {}).values())) + "\n"
    try:
        from app.operators import service as opsvc
        op_examples = opsvc.reference_block("REGULAR")
    except Exception:
        op_examples = ""
    return (f"{head}{task}=== AVAILABLE OPERATORS ===\n{op_summary}\n\n{op_examples}"
            f"=== DATAFIELDS (type|category tagged) ===\n{_field_summary(fields, categories)}\n\n{guide}{rules}{div}")


def rewrite_master_prompt(*, raw, region, delay, instrument, universe, dataset_names, fields,
                          categories, max_operators, n) -> str:
    """A clean, well-structured INSTRUCTION the user can review/edit — role → objective → the full
    hypotheses/strategy VERBATIM → high-level directives. It deliberately does NOT dump the operator
    list or the datafield list: those (and the exact field types/categories) are injected BEHIND THE
    SCENES at generation time. Nothing in the notes is summarised away."""
    d0 = (" For delay 0, favour ideas with a strong risk-adjusted return (Sharpe)." if str(delay) == "0" else "")
    return (
        "## Role\n"
        "You are a senior quantitative researcher and WorldQuant BRAIN alpha engineer with a risk-aware "
        "portfolio manager's judgement. Think mechanism-first: what real economic force each idea captures and "
        "why it should persist.\n\n"
        "## Objective\n"
        f"Generate distinct, high-quality FastExpr alpha expressions that TEST every hypothesis/idea below and add "
        f"genuine, low-correlation value in {region} ({instrument}, delay {delay})." + d0 + " Spread the batch "
        "across ALL the available datasets and across DIFFERENT mechanisms — do not fixate on one.\n\n"
        "## Hypotheses / Strategy (test each one; any example expression is an ILLUSTRATION of the mechanism, "
        "NOT a restriction on which datafields to use)\n"
        + (raw or "").strip() + "\n\n"
        "## Directives\n"
        "- Use ONLY the datafields you are given; never invent one. Wrap any VECTOR field in a vec_* operator "
        "before use; use MATRIX fields directly.\n"
        "- Every expression must express a DIFFERENT mechanism/operator family. Prefer real transformations over "
        "filler — no identity arithmetic (no * 1, + 0, / 1).\n"
        "- Pass named operator parameters as keywords; groups are identifiers, never numbers.\n"
        "The exact operator list (with correct signatures/examples) and the available datafields are provided "
        "separately at generation time — follow them precisely."
    )


# ── run ──────────────────────────────────────────────────────────────────────────────

def run_generation(*, mode, prompt, region, delay, instrument, universe, dataset_names,
                   fields, categories, max_operators, n, repair_rounds, region_note, progress,
                   raw_prompt=False) -> dict:
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("alpha_generation")
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    progress(message=f"generating with {', '.join(p.name for p in chain)}…")
    val = _validator(fields, max_operators, multi_field=(mode == "multi"))
    if raw_prompt and prompt.strip():
        # A user-authored / auto-rewritten master prompt: send it as-is, only guaranteeing the
        # output contract. Validation against the provided fields still applies.
        full = prompt.strip()
        if "json array" not in full.lower():
            full += f"\n\nReturn ONLY a JSON array of exactly {n} expression strings."
    else:
        full = build_prompt(mode=mode, prompt=prompt, region=region, delay=delay, instrument=instrument,
                            universe=universe, dataset_names=dataset_names, fields=fields,
                            categories=categories, max_operators=max_operators, n=n, region_note=region_note)
    from app.knowledge.service import memory_prompt_context
    mem = memory_prompt_context(prompt, region=region, fields=fields, datasets=dataset_names, limit=8)
    if mem:
        full = full + "\n\n" + mem
    multi = __import__("app.core.llm_router", fromlist=["TaskLLM"]).TaskLLM("alpha_generation")
    out = L.generate_valid(multi, val, full, repair_rounds=repair_rounds)

    valid = out.get("valid", [])
    rejected = list(out.get("rejected", []))
    field_ids = {f["id"] for f in fields}

    # Operator keyword / semantic enforcement (keyword-required args, bucket-as-group,
    # densify-on-group, ts_regression x!=y) — reject anything the LLM got wrong.
    specs = _param_specs()
    kept2 = []
    for e in valid:
        extra = operator_issues(e, specs)
        if extra:
            rejected.append((e, [type("I", (), {"code": c})() for c, _ in extra]))
        else:
            kept2.append(e)
    valid = kept2

    # Hard ≤2-category guard for multi-field (belt-and-braces beyond the prompt).
    kept = []
    if mode == "multi" and categories:
        for e in valid:
            cats = {categories.get(fid) for fid in _fields_in(e, field_ids) if categories.get(fid)}
            if len(cats) > 2:
                rejected.append((e, [type("I", (), {"code": "MULTI_CATEGORY"})()]))
            else:
                kept.append(e)
        valid = kept

    # Diversity credit + report.
    all_ops, all_fields = [], []
    for e in valid:
        all_ops += _operators_in(e)
        all_fields += _fields_in(e, field_ids)
    if valid:
        try:
            record_usage(region, list(set(all_ops)), list(set(all_fields)))
        except Exception:
            pass

    progress(message=f"{len(valid)} valid via {out.get('provider')}")
    return {
        "valid": valid,
        "rejected": [{"expr": e, "issues": [getattr(i, "code", str(i)) for i in iss]} for e, iss in rejected],
        "provider": out.get("provider"), "model": out.get("model"),
        "report": {"total": len(valid) + len(rejected), "valid": len(valid), "rejected": len(rejected)},
        "operators_used": sorted(set(all_ops)),
        "fields_used": sorted(set(all_fields)),
    }


def validate_expressions(exprs: list, fields: list, max_operators: int, multi_field: bool) -> dict:
    val = _validator(fields, max_operators, multi_field)
    specs = _param_specs()
    results = []
    for e in exprs:
        r = val.validate(e)
        extra = operator_issues(e, specs) if r.ok else []
        ok = r.ok and not extra
        issues = [{"code": i.code, "message": i.message} for i in r.issues] + \
                 [{"code": c, "message": m} for c, m in extra]
        results.append({"expr": e, "ok": ok, "issues": issues})
    valid = sum(1 for x in results if x["ok"])
    return {"results": results, "report": {"total": len(exprs), "valid": valid, "rejected": len(exprs) - valid}}


_SLOT_RE = __import__("re").compile(r"\{(field\d*)(?::([A-Za-z]+))?\}")


def expand_templates(templates: list, field_ids: list, fields: list, vec_ops: list,
                     max_operators: int, multi_field: bool, max_combos: int = 60,
                     field2_ids: list | None = None) -> dict:
    """Expand {field} templates over the selected fields. VECTOR fields fan out across each
    vec_* reduction; multi-field templates ({field2}) take a bounded Cartesian product, drawing
    {field2} from field2_ids when given (so the user can pick a different dataset — or their own
    fields — for the second slot). Everything is validated."""
    from itertools import product
    types = {f["id"]: str(f.get("type", "MATRIX")).upper() for f in fields}
    pool2 = field2_ids or field_ids   # {field2} pool; default to the same fields

    def variants(fid):
        if types.get(fid, "MATRIX") == "VECTOR":
            return [f"{op}({fid})" for op in (vec_ops or ["vec_avg"])]
        return [fid]

    def pool_for(name: str) -> list:
        return pool2 if name == "field2" else field_ids

    exprs, seen = [], set()
    cap = max(1, min(int(max_combos or 60), 1000))
    for t in [x for x in templates if x.strip()]:
        names = sorted({n for n, _ in _SLOT_RE.findall(t)})
        if not names:
            if t not in seen:
                seen.add(t); exprs.append(t)
            continue
        if len(names) == 1:
            for fid in pool_for(names[0]):
                for v in variants(fid):
                    e = _SLOT_RE.sub(lambda m: v, t)
                    if e not in seen:
                        seen.add(e); exprs.append(e)
        else:
            pools = [pool_for(nm) for nm in names]
            n = 0
            for combo in product(*pools):
                if len(set(combo)) == 1:
                    continue
                mapping = {names[i]: variants(combo[i])[0] for i in range(len(names))}
                e = _SLOT_RE.sub(lambda m: mapping[m.group(1)], t)
                if e not in seen:
                    seen.add(e); exprs.append(e); n += 1
                if n >= cap:
                    break
    # Validate against ALL fields that can appear (field1 + field2 pools + any custom).
    val = _validator(fields, max_operators, multi_field)
    specs = _param_specs()
    results = []
    for e in exprs:
        r = val.validate(e)
        extra = operator_issues(e, specs) if r.ok else []
        ok = r.ok and not extra
        results.append({"expr": e, "ok": ok, "issues": [i.code for i in r.issues] + [c for c, _ in extra]})
    valid = sum(1 for x in results if x["ok"])
    return {"expressions": exprs, "results": results,
            "report": {"total": len(exprs), "valid": valid, "rejected": len(exprs) - valid}}


def suggest_templates(*, region, delay, instrument, universe, dataset_names, fields, categories,
                      max_operators, n, multi_field, progress) -> dict:
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("alpha_generation")
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    n_ops = _clamp_ops(max_operators)
    slot = ("Every template MUST contain BOTH {field} and {field2} (a two-field idea) — this is the "
            "MULTI-FIELD mode, so make the two placeholders interact meaningfully (ratio, spread, "
            "correlation, conditioning). Produce MANY structurally different two-field combinations."
            if multi_field
            else "Each template MUST contain {field} EXACTLY once and MUST NOT contain {field2}.")
    op_summary = operator_summary()
    try:
        from app.operators import service as opsvc
        op_examples = opsvc.reference_block("REGULAR")
    except Exception:
        op_examples = ""
    meta = (
        "Design reusable WorldQuant FastExpr alpha TEMPLATES.\n"
        f"Region {region}, universe {universe}, delay {delay}, {instrument}. Datasets: {', '.join(dataset_names) or 'unspecified'}.\n"
        f"Propose {n} templates. {slot} Use EXACTLY {n_ops} operators per template.\n"
        "CRITICAL: a template is an abstract pattern, so use ONLY the literal placeholders {field}"
        + (" and {field2}" if multi_field else "") + " where a datafield goes. NEVER write an actual "
        "field id, dataset name, or any concrete data value in a template — placeholders only.\n"
        "NO FILLER ARITHMETIC: never pad a template with identity operations — no '* 1', '+ 0', '/ 1', "
        "'- 0', '0 *', '1 /'. Every arithmetic operator must CHANGE the signal (a real ratio, spread, or "
        "sum of two DIFFERENT transformed terms), otherwise use none.\n"
        "PASS NAMED PARAMETERS AS KEYWORDS (e.g. keep(x, f, period=5), ts_decay_linear(x, 20, dense=false)); "
        "windows/groups are positional; groups are identifiers (industry/sector/…), never numbers.\n"
        "DIVERSIFY: every template a DIFFERENT economic idea AND operator family. NEVER use bucket() or any "
        "vec_* operator (the placeholder is substituted with an already-reduced field, so VECTOR handling is "
        "applied at expansion time).\n"
        f"=== OPERATORS ===\n{op_summary}\n{op_examples}"
        f"=== DATAFIELDS (for context only — do NOT put these ids in a template) ===\n{_field_summary(fields, categories)}\n"
        "Return ONLY a JSON array of template strings using placeholders only.")
    from app.knowledge.service import memory_prompt_context
    mem = memory_prompt_context(f"{region} {universe} template design", region=region, fields=fields, datasets=dataset_names, limit=8)
    if mem:
        meta = meta + "\n\n" + mem
    progress(message="suggesting templates…")
    res = __import__("app.core.llm_router", fromlist=["TaskLLM"]).TaskLLM("alpha_generation").generate_list(meta, n=n)
    import re as _re
    banned = _re.compile(r"\bbucket\s*\(|\bvec_[A-Za-z0-9_]*\s*\(", _re.IGNORECASE)
    # identity arithmetic: `* 1`, `+ 0`, `/ 1`, `- 0`, `0 *`, `1 /` and similar padding
    filler = _re.compile(r"([*/+\-]\s*[01](?![.\d])|(?<![.\d])[01]\s*[*/])")
    field_ids = {str(f.get("id")) for f in fields if f.get("id")}

    def literal_free(t: str) -> bool:
        toks = set(_re.findall(r"[A-Za-z_][A-Za-z0-9_]*", t))
        return not (toks & field_ids)

    need2 = "{field2}" if multi_field else None
    tmpls = [t.strip() for t in res.expressions
             if "{field}" in t and (need2 is None or need2 in t)
             and not banned.search(t) and not filler.search(t) and literal_free(t)]
    return {"templates": tmpls[:max(1, n * 2)], "provider": res.provider, "model": res.model}


def operator_summary() -> str:
    """Compact REGULAR-operator reference (name + signature) other LLM screens can inject so
    the model only uses operators that actually exist with correct arity."""
    ops = engine.operators_df()
    scoped = ops[ops["scope"] == "REGULAR"] if ("scope" in ops.columns and (ops["scope"] == "REGULAR").any()) else ops
    return wqb_llm._build_operator_summary(scoped)


def operators_list() -> list:
    reg = _registry("REGULAR")
    return [{"name": n, "min": s.min_args, "max": s.max_args, "params": s.param_names}
            for n, s in sorted(reg._sigs.items())]


def sandbox_validate(expr: str) -> dict:
    """Operator-sandbox check: validates syntax, operator existence/arity, keyword args and the
    semantic rules — but NOT field membership (any field name is allowed), so a user can test an
    operator's usage without first selecting matching datafields."""
    val = V.Validator(_registry("REGULAR"), known_fields=None, field_types={},
                      check_unknown_kwargs=True, require_single_field=False,
                      op_count_ignore_prefixes=("vec_",))
    r = val.validate(expr)
    extra = operator_issues(expr, _param_specs())
    ok = r.ok and not extra
    return {"ok": ok, "issues": [{"code": i.code, "message": i.message} for i in r.issues]
            + [{"code": c, "message": m} for c, m in extra]}


_TEMPLATIZE_SKIP = _GROUP_IDENTS | {"true", "false", "nan", "inf", "none"}


def templatize(expr: str) -> dict:
    """Turn a concrete expression back into a TEMPLATE: replace each datafield with {field}/
    {field2}/… placeholders, and map each field to its dataset (from the knowledge DB) so the user
    knows which dataset to retry with. Operators, group identifiers and numbers are left alone."""
    import re as _re
    try:
        ast = V.parse(expr)
    except Exception:
        return {"template": expr, "fields": [], "datasets": [], "multi": False, "error": "could not parse"}
    # Operators are Call.name strings (not Ident nodes), so any Ident here is a datafield, a group
    # identifier or a literal — no operator list / network needed.
    names, stack = [], [ast]
    while stack:
        n = stack.pop()
        if isinstance(n, V.Ident):
            nm = n.name
            if nm.lower() not in _TEMPLATIZE_SKIP:
                names.append(nm)
        elif isinstance(n, V.Call):
            stack.extend(n.args)
        elif isinstance(n, V.KwArg):
            stack.append(n.value)
        elif isinstance(n, V.Unary):
            stack.append(n.operand)
        elif isinstance(n, V.BinOp):
            stack.extend([n.left, n.right])
        elif isinstance(n, V.Seq):
            stack.extend(n.statements)
    distinct = list(dict.fromkeys(names))
    mapping = {f: ("{field}" if i == 0 else "{field2}" if i == 1 else "{field%d}" % (i + 1))
               for i, f in enumerate(distinct)}
    template = expr
    for f, ph in mapping.items():
        template = _re.sub(r"(?<![\w.])" + _re.escape(f) + r"(?![\w])", ph, template)

    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select
    fields_out = []
    with SessionLocal() as db:
        for f in distinct:
            row = db.scalar(select(M.Field).where(M.Field.field_id == f))
            fields_out.append({"id": f, "dataset_id": (row.dataset_id if row else ""), "type": (row.type if row else "MATRIX")})
    return {"template": template, "fields": fields_out, "multi": len(distinct) > 1,
            "datasets": list(dict.fromkeys(x["dataset_id"] for x in fields_out if x["dataset_id"]))}


# ── prompt library ───────────────────────────────────────────────────────────────────

def list_prompts(scope: str = "") -> list:
    with SessionLocal() as db:
        q = select(M.Prompt).order_by(M.Prompt.created_at.desc())
        if scope:
            q = q.where(M.Prompt.scope == scope)
        rows = db.scalars(q.limit(100)).all()
        import json
        return [{"id": r.id, "name": r.name, "scope": r.scope, "category": r.category,
                 "region": r.region, "body": r.body, "created_at": r.created_at,
                 "datasets": json.loads(r.datasets_json or "[]")} for r in rows]


def delete_prompt(pid: int) -> None:
    with SessionLocal() as db:
        row = db.get(M.Prompt, pid)
        if row:
            db.delete(row)
            db.commit()
