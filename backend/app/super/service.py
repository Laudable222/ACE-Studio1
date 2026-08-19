"""SuperAlpha engine — kept as its OWN capability, separate from regular generation and
simulation. A SuperAlpha combines the user's EXISTING alphas: a selection expression
picks which alphas take part (filtered on alpha attributes, not datafields) and a combo
expression weights them.

Reuses the studio validator's SELECTION/COMBO support, its verified selection-attribute
vocabulary, and ace_lib (construct_selection_expression, run_selection, generate_alpha
alpha_type=SUPER, simulate_single_alpha). SuperAlphas cannot be multi-simulated and BRAIN
caps concurrency at 3.
"""

from __future__ import annotations

import json
import math
import re
import time
from functools import partial
from itertools import product
from multiprocessing.pool import ThreadPool

from app.brain import engine
import ace_lib as ace          # noqa: E402
import validator as V          # noqa: E402
import llm_providers as L      # noqa: E402
import keys as keymgr          # noqa: E402
import wqb_llm                 # noqa: E402

from app.simulation.service import gate_thresholds, _num, _norm_turnover, _corr_from_submission

SUPER_MAX_CONCURRENCY = 3
SUPER_MIN_COMPONENTS = 10
_HAS_OWN = re.compile(r'(?<![\w."\'])own(?![\w."\'])')
_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# The stats members that generate_stats(alpha) actually exposes (attribute access). Kept in
# ONE place so the combo prompt and the combo guard never drift apart.
STAT_MEMBERS = ["returns", "trade_pnl", "trade_shares", "trade_value", "hold_pnl", "hold_value",
                "hold_shares", "turnover", "drawdown", "long_count", "long_value", "short_count", "short_value"]

# The exact alpha-selection attribute vocabulary (from the BRAIN selection-features table), so the
# LLM references real properties with correct types/enums instead of guessing.
SELECTION_ATTR_REFERENCE = (
    "=== ALPHA SELECTION ATTRIBUTES (use these EXACT names, types and forms) ===\n"
    "category  — enum: NONE|PRICE_REVERSION|PRICE_MOMENTUM|VOLUME|FUNDAMENTAL|ANALYST|PRICE_VOLUME|RELATION|SENTIMENT  (category == \"FUNDAMENTAL\")\n"
    "color  — enum: NONE|RED|YELLOW|GREEN|BLUE|PURPLE  (color == \"GREEN\")\n"
    "dataset  — string, membership: in(datasets, \"fundamental6\")\n"
    "datafields  — string, membership: in(datafields, \"returns\")\n"
    "datacategories  — enum in: analyst|broker|earnings|fundamental|imbalance|insiders|institutions|macro|model|news|option|other|pv|risk|sentiment|shortinterest|socialmedia  (in(datacategories, \"news\") / not(in(datacategories, \"fundamental\")))\n"
    "decay  — numeric (decay <= 2)   truncation — numeric (truncation <= 0.06)   turnover — numeric IS turnover (turnover < 0.30)\n"
    "operator_count — numeric (operator_count < 10)   dataset_count — numeric   datafield_count — numeric   datacategory_count — numeric\n"
    "long_count / short_count — numeric IS avg instrument counts (long_count > 600)\n"
    "neutralization — enum: NONE|MARKET|SECTOR|INDUSTRY|SUBINDUSTRY   universe — enum: TOP200|TOP500|TOP1000|TOP2000|TOP3000\n"
    "universe_size(universe) — numeric operator over the universe string (universe_size(universe) >= 2000)\n"
    "favorite — 1/0 (not(favorite))   name — exact string   tags — string membership: in(tags, \"my_tag\")\n"
    "self_correlation / prod_correlation — numeric (self_correlation <= 0.6)   os_start_date — YYYY-MM-DD string (os_start_date > \"2020-01-01\")\n"
    "classifications — in(classifications, \"POWER_POOL\"|\"ATOM\")   competitions — in(competitions, \"HCAC2025\")\n"
    "SuperAlpha author features (numeric): author_yield_rate, author_quarter_yield_rate, author_tenure, author_activity, "
    "author_distinct_count_regions, author_distinct_count_datasetcategory, author_distinct_count_dataset, "
    "author_distinct_count_datafield, author_distinct_count_operator, author_distinct_quarter_count_datasetcategory, "
    "author_distinct_quarter_count_dataset, author_distinct_quarter_count_datafield, author_distinct_quarter_count_operator, "
    "author_prod_correlation, author_self_correlation, author_sharpe, author_turnover, author_fitness, author_returns_to_drawdown.\n"
)


def _registry(kind: str) -> V.OperatorRegistry:
    ops = engine.operators_df()
    df = ops[ops["scope"] == kind] if ("scope" in ops.columns and (ops["scope"] == kind).any()) else ops
    return V.OperatorRegistry.from_dataframe(df)


def _validator(kind: str) -> V.Validator:
    return V.build_super_validator(_registry(kind.upper()), kind=kind.upper())


def vocab() -> dict:
    sel = V.SELECTION_VARIABLES
    sel_vars = list(sel.keys()) if isinstance(sel, dict) else list(sel)
    out = {"selection_variables": sel_vars,
           "selection_variable_help": sel if isinstance(sel, dict) else {},
           "selection_non_variables": list(V.SELECTION_NON_VARIABLES),
           "combo_variables": list(V.COMBO_VARIABLES),
           "min_components": SUPER_MIN_COMPONENTS, "max_concurrency": SUPER_MAX_CONCURRENCY,
           "selection_ops": [], "combo_ops": [], "source": "default"}
    try:
        out["selection_ops"] = sorted(_registry("SELECTION")._sigs.keys())
        out["combo_ops"] = sorted(_registry("COMBO")._sigs.keys())
        out["source"] = "account"
    except Exception:
        pass
    return out


# ── selection template expansion (category × zipped bands) ───────────────────────────

def expand(templates: list, variables: dict, paired: list, cap: int = 500) -> dict:
    tmpls = [t.strip() for t in templates if t.strip()]
    if not tmpls:
        return {"error": "Add at least one selection template first."}
    paired_groups = [[v for v in g if v in variables] for g in (paired or [])]
    paired_groups = [g for g in paired_groups if len(g) > 1]
    grouped = {v for g in paired_groups for v in g}
    exprs, seen, truncated = [], set(), False
    for t in tmpls:
        used = set(_VAR_RE.findall(t))
        if not used:
            if t not in seen:
                seen.add(t); exprs.append(t)
            continue
        missing = sorted(u for u in used if u not in variables)
        if missing:
            return {"error": f"Template uses {{{missing[0]}}} but no values were given for it."}
        axes, axis_vars = [], []
        for g in paired_groups:
            g = [v for v in g if v in used]
            if len(g) < 2:
                continue
            m = min(len(variables[v]) for v in g)
            axes.append([tuple(variables[v][i] for v in g) for i in range(m)]); axis_vars.append(g)
        for v in sorted(used - grouped):
            axes.append([(x,) for x in variables[v]]); axis_vars.append([v])
        for combo in product(*axes) if axes else [()]:
            mapping = {}
            for names, vals in zip(axis_vars, combo):
                mapping.update(dict(zip(names, vals)))
            e = _VAR_RE.sub(lambda mm: str(mapping.get(mm.group(1), mm.group(0))), t)
            if e not in seen:
                seen.add(e); exprs.append(e)
            if len(exprs) >= cap:
                truncated = True; break
        if truncated:
            break
    val = _validator("SELECTION")
    results = [{"expr": e, "ok": (r := val.validate(e)).ok, "issues": [i.code for i in r.issues]} for e in exprs]
    return {"expressions": exprs, "results": results, "truncated": truncated}


def validate(selections: list, combos: list) -> dict:
    def check(exprs, kind):
        val = _validator(kind)
        return [{"expr": e, "ok": (r := val.validate(e)).ok,
                 "issues": [{"code": i.code, "message": i.message} for i in r.issues]} for e in exprs]
    return {"selection": check(selections, "SELECTION"), "combo": check(combos, "COMBO")}


# ── selection preflight (count component alphas) ─────────────────────────────────────

def selection_preview(selections, region, delay, instrument, selection_limit, selection_handling,
                      min_count, progress, should_cancel) -> dict:
    s = engine.require_session()
    val = _validator("SELECTION")
    good, rejected = val.partition([e.strip() for e in selections if e.strip()])
    progress(total=len(good), message=f"checking {len(good)} selection(s)"
             + (f" · {len(rejected)} rejected" if rejected else ""))
    rows = []
    for i, sel in enumerate(good):
        if should_cancel():
            break
        data = ace.construct_selection_expression(sel, instrument_type=instrument, region=region,
                                                  delay=delay, selection_limit=selection_limit,
                                                  selection_handling=selection_handling)
        try:
            res = ace.run_selection(s, data)
            count, msg = res.get("selected_alphas_count"), res.get("message") or ""
        except Exception as e:  # noqa: BLE001
            count, msg = None, f"error: {str(e).splitlines()[0][:120]}"
        usable = isinstance(count, int) and count >= min_count
        rows.append({"selection": sel, "count": count, "message": msg, "usable": usable})
        progress(done=i + 1, message=f"{i + 1}/{len(good)} · {sum(1 for r in rows if r['usable'])} usable")
    rows.sort(key=lambda r: -(r["count"] or 0))
    return {"checked": len(rows), "usable": sum(1 for r in rows if r["usable"]), "min_count": min_count,
            "rejected": [{"expr": e, "issues": [i.code for i in iss]} for e, iss in rejected], "results": rows}


# ── LLM suggestion ───────────────────────────────────────────────────────────────────

def _scoped_ops(scope):
    ops = engine.operators_df()
    return ops[ops["scope"] == scope] if ("scope" in ops.columns and (ops["scope"] == scope).any()) else ops


def _op_examples(scope: str) -> str:
    """Operator Lab usage examples for a scope (SELECTION/COMBO), best-effort."""
    try:
        from app.operators import service as opsvc
        blk = opsvc.reference_block(scope)
        return (blk + "\n") if blk else ""
    except Exception:
        return ""


def suggest(kind, region, delay, instrument, universe, n, own, progress) -> dict:
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("alpha_generation")
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    if kind == "selection":
        cats = []
        try:
            from app.knowledge import service as knowledge_service
            rows = knowledge_service.catalogue_datasets(region, delay, instrument, universe)
            cats = sorted({str(r.get("category")) for r in rows if r.get("category")})
        except Exception:
            pass
        op_summary = wqb_llm._build_operator_summary(_scoped_ops("SELECTION"))
        own_rule = ("EVERY selection MUST begin with the bare token `own &&` so ONLY the user's own alphas "
                    "are selected. When filtering to `own`, keep the rest SIMPLE — one or two plain conditions "
                    "(e.g. `own && turnover < 0.3`); do NOT stack complicated multi-clause filters.\n"
                    if own else "")
        meta = ("You are writing SELECTION expressions for a WorldQuant BRAIN SuperAlpha — a boolean filter over "
                "ALPHA ATTRIBUTES (not datafields) choosing which of the user's alphas combine.\n"
                f"Region {region}, delay {delay}, {instrument}. Categories: {', '.join(cats) or 'unknown'}.\n"
                + SELECTION_ATTR_REFERENCE +
                f"These do NOT exist and are rejected: {', '.join(V.SELECTION_NON_VARIABLES)} — there is NO way "
                "to filter on live performance (sharpe/fitness/returns) except the author_* aggregates above.\n"
                f"Propose {n} DIVERSE selections. " + own_rule +
                f"Each needs >= {SUPER_MIN_COMPONENTS} components, so keep them broad. String "
                'values in double quotes (in(datacategories, "news")), numbers bare (turnover < 0.3).\n'
                f"=== SELECTION OPERATORS ===\n{op_summary}\n{_op_examples('SELECTION')}Return ONLY a JSON array of selection strings.")
    else:
        op_summary = wqb_llm._build_operator_summary(_scoped_ops("COMBO"))
        meta = ("You are writing COMBO expressions for a WorldQuant BRAIN SuperAlpha — they weight the selected "
                "component alphas. Combos operate on the component `alpha` and its STATISTICS, never on datafields.\n"
                "HARD RULES — follow EXACTLY, anything else is rejected:\n"
                "1. The ONLY way to get statistics is `s = generate_stats(alpha)`. generate_stats takes EXACTLY "
                "ONE argument, the bare token `alpha` — never a number, field, or anything else.\n"
                "2. The stats object `s` exposes ONLY these members (attribute access): "
                + ", ".join("s." + m for m in STAT_MEMBERS) +
                ". NEVER invent any other stat (no s.fitness, s.sharpe, s.ir, …) — that is a hallucination.\n"
                "3. Build the weight from those stats using the COMBO operators listed below — but NEVER any "
                "group_* operator (groups don't exist for combos).\n"
                "4. Allowed shapes: `1` (equal weight); the predefined `combo_a`; or "
                "`s = generate_stats(alpha); <op>(s.<member>, <window>)` where the LAST statement is the weight, "
                "e.g. `s = generate_stats(alpha); ts_arg_max(s.returns, 120)` or "
                "`s = generate_stats(alpha); ts_ir(s.returns, 500)`.\n"
                f"Propose {n} DIVERSE, VALID combos following the shapes above.\n"
                f"=== COMBO OPERATORS (non-group only) ===\n{op_summary}\n{_op_examples('COMBO')}Return ONLY a JSON array of combo strings.")
    progress(message=f"writing {kind} expressions…")
    res = __import__("app.core.llm_router", fromlist=["TaskLLM"]).TaskLLM("alpha_generation").generate_list(meta, n=n)
    exprs = [e.strip() for e in res.expressions if e.strip()]
    if kind == "selection" and own:
        exprs = [e if _HAS_OWN.search(e) else f"own && {e}" for e in exprs]
    if kind == "combo":
        import re as _re
        def _combo_ok(e: str) -> bool:
            if _re.search(r"\bgroup_\w+\s*\(", e, _re.I):         # no group operators in combos
                return False
            for arg in _re.findall(r"generate_stats\s*\(([^)]*)\)", e, _re.I):
                if arg.strip() != "alpha":                       # generate_stats takes ONLY alpha
                    return False
            for member in _re.findall(r"\bstats?\s*\.\s*(\w+)", e) + _re.findall(r"\bs\s*\.\s*(\w+)", e):
                if member.lower() not in STAT_MEMBERS:                   # no hallucinated stats
                    return False
            return True
        exprs = [e for e in exprs if _combo_ok(e)] or ["1"]      # never return nothing
    val = _validator(kind.upper())
    valid, rejected = val.partition(exprs)
    return {"kind": kind, "expressions": valid, "provider": res.provider, "model": res.model,
            "rejected": [{"expr": e, "issues": [i.code for i in iss]} for e, iss in rejected]}


# ── simulation (single, capped at 3) + gate ──────────────────────────────────────────

def _sim_one(s, cfg):
    try:
        resp = ace.start_simulation(s, cfg)
    except Exception as e:  # noqa: BLE001
        return {"alpha_id": None, "simulate_data": cfg, "error": str(e).splitlines()[0][:200]}
    if resp.status_code // 100 != 2:
        detail = (resp.text or "").strip()[:200]
        if resp.status_code == 429 and "CONCURRENT_SIMULATION_LIMIT" in detail:
            detail = "BRAIN concurrent-simulation limit hit — lower concurrency or wait."
        return {"alpha_id": None, "simulate_data": cfg, "error": detail or f"HTTP {resp.status_code}"}
    try:
        url = resp.headers["Location"]
    except KeyError:
        return {"alpha_id": None, "simulate_data": cfg, "error": "no progress URL"}
    last = {}
    while True:
        pr = s.get(url)
        if pr.status_code // 100 != 2:
            return {"alpha_id": None, "simulate_data": cfg, "error": f"progress HTTP {pr.status_code}"}
        if not float(pr.headers.get("Retry-After", 0) or 0):
            last = pr.json(); break
        time.sleep(float(pr.headers["Retry-After"]))
    if last.get("status", "ERROR") == "ERROR" or not last.get("alpha"):
        msg = re.sub(r"<[^>]+>", "", str(last.get("message") or "simulation failed")).strip()
        where = (last.get("location") or {}).get("property")
        return {"alpha_id": None, "simulate_data": cfg, "error": f"{where}: {msg}" if where else msg}
    result = ace.get_simulation_result_json(s, last["alpha"])
    if not result:
        return {"alpha_id": None, "simulate_data": cfg, "error": "no result for alpha"}
    return {"alpha_id": result["id"], "simulate_data": cfg}


def run_simulation(*, selections, combos, region, delay, instrument, universes, neutralizations,
                   decay, truncation, test_period, pasteurization, unit_handling, nan_handling, max_trade,
                   selection_limit, selection_handling, concurrency, max_turnover, min_sharpe, min_fitness,
                   max_corr, tag, winner_tag, winner_color, tag_winners_above, check_submission,
                   progress, should_cancel) -> dict:
    import multiprocessing
    from app.db.base import SessionLocal
    from app.db import models as M
    s = engine.require_session()
    sel_val, combo_val = _validator("SELECTION"), _validator("COMBO")
    good_sel, bad_sel = sel_val.partition([e.strip() for e in selections if e.strip()])
    good_combo, bad_combo = combo_val.partition([e.strip() for e in combos if e.strip()] or ["1"])
    if not good_sel:
        raise RuntimeError("No valid selection expression.")
    if not good_combo:
        raise RuntimeError("No valid combo expression ('1' always works).")
    universes = universes or ["ILLIQUID_MINVOL1M"]
    neutralizations = neutralizations or ["FAST"]
    alpha_list = [
        ace.generate_alpha(alpha_type="SUPER", selection=sel, combo=combo, region=region, universe=u,
                           delay=delay, decay=decay, neutralization=nn, truncation=truncation,
                           pasteurization=pasteurization, test_period=test_period, unit_handling=unit_handling,
                           nan_handling=nan_handling, max_trade=max_trade, selection_limit=selection_limit,
                           selection_handling=selection_handling)
        for sel in good_sel for combo in good_combo for u in universes for nn in neutralizations
    ]
    conc = max(1, min(SUPER_MAX_CONCURRENCY, concurrency))
    progress(total=len(alpha_list), message=f"{len(alpha_list)} SuperAlpha(s) · conc {conc} (no multi-sim)")
    flat, done, cancelled, errors = [], 0, False, []
    with ThreadPool(conc) as pool:
        it = pool.imap_unordered(partial(_sim_one, s), alpha_list)
        while True:
            if should_cancel():
                cancelled = True; pool.terminate(); break
            try:
                res = it.next(timeout=1)
            except StopIteration:
                break
            except multiprocessing.TimeoutError:
                continue
            except Exception as e:  # noqa: BLE001
                msg = str(e).splitlines()[0][:160]
                if msg not in errors:
                    errors.append(msg)
                continue
            flat.append(res); done += 1
            why = res.get("error")
            if why and why not in errors:
                errors.append(why)
            progress(done=done, log=f"{done}/{len(alpha_list)} · {'alpha ' + res['alpha_id'] if res.get('alpha_id') else 'FAILED — ' + (why or '?')}")

    ok = [x for x in flat if x.get("alpha_id")]
    if not ok:
        return {"configs": len(alpha_list), "simulated": 0, "passed": 0, "cancelled": cancelled,
                "errors": errors[:20], "results": []}

    sim_cfg = {"check_submission": check_submission, "check_self_corr": check_submission, "check_prod_corr": check_submission}
    progress(message=f"fetching stats for {len(ok)}…")

    def fetch(x):
        try:
            return ace.get_specified_alpha_stats(s, x["alpha_id"], x["simulate_data"], **sim_cfg)
        except Exception:
            return {"alpha_id": x.get("alpha_id"), "simulate_data": x.get("simulate_data"), "is_stats": None, "is_tests": None}
    stats_list = []
    with ThreadPool(3) as pool:
        for r in pool.imap(fetch, ok):
            stats_list.append(r)
    result = ace._delete_duplicates_from_result(stats_list)

    rows, passed, tagged = [], 0, 0
    for r in result:
        aid = r.get("alpha_id"); cfg = r.get("simulate_data", {}) or {}
        settings = cfg.get("settings", {}) if isinstance(cfg.get("settings"), dict) else {}
        st = r.get("is_stats")
        sharpe = fitness = turnover = None
        if hasattr(st, "empty") and not st.empty:
            d = st.iloc[0].to_dict()
            sharpe, fitness, turnover = _num(d.get("sharpe")), _num(d.get("fitness")), _num(d.get("turnover"))
        tests = r.get("is_tests")
        tests_failed = int((tests["result"] == "FAIL").sum()) if (hasattr(tests, "empty") and not tests.empty and "result" in tests.columns) else 0
        sub = r.get("check_submission") if check_submission else None
        self_c = _corr_from_submission(sub, "self") if check_submission else None
        prod_c = _corr_from_submission(sub, "prod") if check_submission else None
        pool_c = (_corr_from_submission(sub, "pool") or _corr_from_submission(sub, "power")) if check_submission else None
        turn = _norm_turnover(turnover)
        reasons = []
        if sharpe is None or abs(sharpe) < min_sharpe:
            reasons.append(f"|Sharpe| {abs(sharpe):.2f}<{min_sharpe}" if sharpe is not None else "no Sharpe")
        if fitness is None or abs(fitness) < min_fitness:
            reasons.append(f"|Fitness| {abs(fitness):.2f}<{min_fitness}" if fitness is not None else "no Fitness")
        if turn is not None and turn >= max_turnover:
            reasons.append(f"turnover {turn:.0%}≥{max_turnover:.0%}")
        if tests_failed:
            reasons.append(f"{tests_failed} IS test fail")
        if check_submission:
            for label, val in (("self", self_c), ("prod", prod_c), ("powerpool", pool_c)):
                if val is not None and abs(val) >= max_corr:
                    reasons.append(f"{label}-corr {abs(val):.2f}≥{max_corr}")
        ok_gate = not reasons
        tag_used = ""
        if aid:
            try:
                if ok_gate and fitness is not None and abs(fitness) >= tag_winners_above and winner_tag:
                    ace.set_alpha_properties(s, aid, color=winner_color or "GREEN", tags=[winner_tag]); tag_used = winner_tag; tagged += 1
                elif tag:
                    ace.set_alpha_properties(s, aid, tags=[tag]); tag_used = tag
            except Exception:
                pass
        with SessionLocal() as db:
            db.add(M.SimResult(alpha_id=aid or "", expression=f"{cfg.get('selection','')} -> {cfg.get('combo','')}",
                               region=region, delay=delay, universe=settings.get("universe", ""),
                               neutralization=settings.get("neutralization", ""), sharpe=sharpe, fitness=fitness,
                               turnover=turn, self_corr=self_c, prod_corr=prod_c, powerpool_corr=pool_c,
                               tests_failed=tests_failed, passed_gate=ok_gate, gate_reasons=json.dumps(reasons),
                               tagged=tag_used))
            db.commit()
        if ok_gate:
            passed += 1
        rows.append({"alpha_id": aid, "selection": cfg.get("selection"), "combo": cfg.get("combo"),
                     "universe": settings.get("universe"), "neutralization": settings.get("neutralization"),
                     "sharpe": sharpe, "fitness": fitness, "turnover": turn, "tests_failed": tests_failed,
                     "passed": ok_gate, "reasons": reasons, "tag": tag_used})
    rows.sort(key=lambda x: (-(x["passed"]), -(abs(x["fitness"]) if x["fitness"] is not None else 0)))
    failed = [x for x in flat if not x.get("alpha_id")]
    return {"configs": len(alpha_list), "simulated": len(rows), "passed": passed, "tagged": tagged,
            "failed": len(failed), "cancelled": cancelled, "errors": errors[:20], "results": rows,
            "thresholds": {"sharpe": min_sharpe, "fitness": min_fitness}}
