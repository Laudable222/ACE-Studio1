"""Simulation engine + the success gate.

Simulates generated alphas (regular FastExpr) across the chosen universes and
neutralizations, then judges each against ONE explicit gate. An alpha is a verified
success only if EVERY metric passes (magnitudes, so a negative alpha passes in absolute
value):

  |Sharpe| >= threshold   (delay 0: 2.69, else 1.58)
  |Fitness| >= threshold  (delay 0: 1.5,  else 1.0)
  |Turnover| < 0.70
  in-sample tests: no FAIL
  self / prod / powerpool correlation < 0.70   (only when the submission check is run)

Winners (|fitness| over a user threshold) and every kept alpha are tagged with the user's
OWN chosen tags — nothing hardcoded. Passing/strong alphas also credit the diversity
engine's usage table by |fitness|, so what actually WORKED steers future generation.

Reuses the studio engine (ace_lib) via the brain adapter.
"""

from __future__ import annotations

import json
import math
import re
import time
from functools import partial
from multiprocessing.pool import ThreadPool

from app.brain import engine
import ace_lib as ace  # noqa: E402

from app.db.base import SessionLocal
from app.db import models as M
from app.generation.service import _operators_in, _fields_in, _leaf_idents  # reuse AST walkers

# Fields that exist in every equity region — never worth a per-region lookup (price/volume + the
# standard group fields). Anything else in an expression is verified against the region's data.
_UNIVERSAL = {
    "open", "close", "high", "low", "volume", "vwap", "returns", "cap", "adv20", "sharesout",
    "dividend", "split", "assets", "liabilities", "sales", "ebit", "ebitda",
    "industry", "subindustry", "sector", "market", "country", "exchange", "currency",
}
from sqlalchemy import select, desc, func


def gate_thresholds(delay: int) -> dict:
    """Delay-0 alphas are judged far more strictly on Sharpe (and Fitness)."""
    if int(delay) == 0:
        return {"sharpe": 2.69, "fitness": 1.5}
    return {"sharpe": 1.58, "fitness": 1.0}


def _num(v):
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def _norm_turnover(t):
    if t is None:
        return None
    t = abs(t)
    return t / 100.0 if t > 1.5 else t   # BRAIN may give 0.42 or 42


def _corr_from_submission(df, *needles) -> float | None:
    """Pull a correlation value out of the submission-check DataFrame by fuzzy name match."""
    if df is None or getattr(df, "empty", True) or "name" not in df.columns:
        return None
    for _, row in df.iterrows():
        nm = str(row.get("name", "")).lower()
        if all(n in nm for n in needles):
            return _num(row.get("value"))
    return None


def _credit_usage(region: str, operators: list, fields: list, fitness: float) -> None:
    now = time.time()
    with SessionLocal() as db:
        for kind, keys in (("operator", operators), ("field", fields)):
            for k in keys:
                row = db.scalar(select(M.Usage).where(
                    M.Usage.kind == kind, M.Usage.key == k, M.Usage.region == region))
                if row:
                    row.count += 1
                    row.sum_abs_fitness += abs(fitness or 0)
                    row.last_at = now
                else:
                    db.add(M.Usage(kind=kind, key=k, region=region, count=1,
                                   sum_abs_fitness=abs(fitness or 0), last_at=now))
        db.commit()


def run_simulation(*, expressions, region, delay, universes, neutralizations, decay, truncation,
                   test_period, pasteurization, unit_handling, nan_handling, max_trade, visualization,
                   concurrency, limit_of_multi, max_turnover, min_sharpe, min_fitness, max_corr,
                   tag, winner_tag, winner_color, tag_winners_above,
                   check_submission, get_pnl, get_stats, progress, should_cancel, execution_key="", variant_id=0, experiment_id=0) -> dict:
    import multiprocessing
    s = engine.require_session()
    exprs = [e.strip() for e in expressions if e.strip()]
    if not exprs:
        raise RuntimeError("No expressions to simulate.")

    alpha_list = [
        ace.generate_alpha(expr, alpha_type="REGULAR", region=region, universe=u, delay=delay,
                           decay=decay, neutralization=n, truncation=truncation,
                           pasteurization=pasteurization, test_period=test_period,
                           unit_handling=unit_handling, nan_handling=nan_handling,
                           max_trade=max_trade, visualization=visualization)
        for expr in exprs for u in universes for n in neutralizations
    ]
    conc = max(1, min(8, concurrency))
    multi = max(2, min(10, limit_of_multi))
    batches = [alpha_list[i:i + multi] for i in range(0, len(alpha_list), multi)]
    progress(total=len(alpha_list), message=f"{len(alpha_list)} configs · {len(batches)} batches ×{multi} · conc {conc}",
             log=f"{len(exprs)} expr × {len(universes)} universe × {len(neutralizations)} neut = {len(alpha_list)} configs")

    flat, done, cancelled, errors = [], 0, False, []
    with ThreadPool(conc) as pool:
        it = pool.imap_unordered(partial(ace.simulate_multi_alpha, s), batches)
        while True:
            if should_cancel():
                cancelled = True
                pool.terminate()
                progress(log="STOP requested")
                break
            try:
                res = it.next(timeout=1)
            except StopIteration:
                break
            except multiprocessing.TimeoutError:
                continue
            except Exception as e:  # noqa: BLE001
                msg = str(e).strip().splitlines()[0][:180] if str(e).strip() else type(e).__name__
                if msg not in errors:
                    errors.append(msg)
                progress(log=f"BATCH ERROR: {msg}")
                continue
            flat.extend(res)
            done += len(res)
            progress(done=done, log=f"+{len(res)} simulated ({done}/{len(alpha_list)})")

    ok = [x for x in flat if x.get("alpha_id")]
    if not ok:
        return {"configs": len(alpha_list), "simulated": 0, "passed": 0, "cancelled": cancelled,
                "errors": errors[:20], "results": []}

    sim_cfg = {"get_pnl": get_pnl, "get_stats": get_stats, "check_submission": check_submission,
               "check_self_corr": check_submission, "check_prod_corr": check_submission}
    progress(message=f"fetching stats for {len(ok)} alpha(s)…", log=f"fetching stats ({len(ok)})")

    def fetch(x):
        try:
            return ace.get_specified_alpha_stats(s, x["alpha_id"], x["simulate_data"], **sim_cfg)
        except Exception:
            return {"alpha_id": x.get("alpha_id"), "simulate_data": x.get("simulate_data"),
                    "is_stats": None, "is_tests": None}

    stats_list = []
    with ThreadPool(3) as pool:
        for r in pool.imap(fetch, ok):
            stats_list.append(r)
    result = ace._delete_duplicates_from_result(stats_list)

    # Callers normally pass delay-aware thresholds. Defensively derive them here too so
    # direct/internal callers (not only the HTTP route) can never accidentally pass None.
    defaults = gate_thresholds(delay)
    thr_sharpe = float(defaults["sharpe"] if min_sharpe is None else min_sharpe)
    thr_fit = float(defaults["fitness"] if min_fitness is None else min_fitness)
    rows, passed, tagged = [], 0, 0
    for r in result:
        aid = r.get("alpha_id")
        cfg = r.get("simulate_data", {}) or {}
        settings = cfg.get("settings", {}) if isinstance(cfg.get("settings"), dict) else {}
        expr = cfg.get("regular") or ""
        st = r.get("is_stats")
        sharpe = fitness = turnover = returns = margin = drawdown = None
        if hasattr(st, "empty") and not st.empty:
            d = st.iloc[0].to_dict()
            sharpe, fitness, turnover, returns = _num(d.get("sharpe")), _num(d.get("fitness")), _num(d.get("turnover")), _num(d.get("returns"))
            margin, drawdown = _num(d.get("margin")), _num(d.get("drawdown"))
        tests = r.get("is_tests")
        tests_failed = int((tests["result"] == "FAIL").sum()) if (hasattr(tests, "empty") and not tests.empty and "result" in tests.columns) else 0

        sub = r.get("check_submission") if check_submission else None
        self_c = _corr_from_submission(sub, "self") if check_submission else None
        prod_c = _corr_from_submission(sub, "prod") if check_submission else None
        pool_c = _corr_from_submission(sub, "pool") or _corr_from_submission(sub, "power") if check_submission else None
        sub_fail = bool(hasattr(sub, "empty") and not sub.empty and "result" in sub.columns and (sub["result"] == "FAIL").any())

        turn = _norm_turnover(turnover)
        reasons = []
        if sharpe is None or abs(sharpe) < thr_sharpe:
            reasons.append(f"|Sharpe| {abs(sharpe):.2f}<{thr_sharpe}" if sharpe is not None else "no Sharpe")
        if fitness is None or abs(fitness) < thr_fit:
            reasons.append(f"|Fitness| {abs(fitness):.2f}<{thr_fit}" if fitness is not None else "no Fitness")
        if turn is not None and turn >= max_turnover:
            reasons.append(f"turnover {turn:.0%}≥{max_turnover:.0%}")
        if tests_failed:
            reasons.append(f"{tests_failed} IS test fail")
        if check_submission:
            if sub_fail:
                reasons.append("submission check FAIL")
            for label, val in (("self", self_c), ("prod", prod_c), ("powerpool", pool_c)):
                if val is not None and abs(val) >= max_corr:
                    reasons.append(f"{label}-corr {abs(val):.2f}≥{max_corr}")
        ok_gate = not reasons

        n_ops = len(_operators_in(expr))

        # tag FIRST (winner tag if strong & passing, else the plain tag — both user-chosen),
        # so the applied tag is recorded on the stored row.
        tag_used = ""
        if aid:
            try:
                if ok_gate and fitness is not None and abs(fitness) >= tag_winners_above and winner_tag:
                    ace.set_alpha_properties(s, aid, color=winner_color or "GREEN", tags=[winner_tag])
                    tag_used = winner_tag; tagged += 1
                elif tag:
                    ace.set_alpha_properties(s, aid, tags=[tag])
                    tag_used = tag
            except Exception:
                pass

        # Research memory: persist Alpha DNA, field intelligence and explicit failure reasons.
        try:
            from app.discovery import service as discovery
            dna = discovery.alpha_dna(expr, region)
            with SessionLocal() as db:
                existing = db.scalars(select(M.AlphaDNA)).all()
                sig_fields = set(dna.get("fields", [])); sig_ops = set(dna.get("operators", []))
                similarities = []
                for prev in existing:
                    pf = set(json.loads(prev.fields_json or "[]")); po = set(json.loads(prev.operators_json or "[]"))
                    union = sig_fields | pf | sig_ops | po
                    if union:
                        similarities.append((len((sig_fields|sig_ops)&(pf|po))/len(union)))
                novelty = 1.0 - max(similarities, default=0.0)
                row = db.scalar(select(M.AlphaDNA).where(M.AlphaDNA.expression_key == dna["expression_key"]))
                if not row:
                    row=M.AlphaDNA(expression_key=dna["expression_key"], expression=expr, region=region,
                                   fields_json=json.dumps(dna["fields"]), operators_json=json.dumps(dna["operators"]),
                                   categories_json=json.dumps(dna.get("categories",[])), structure_json=json.dumps(dna["structure"]),
                                   novelty=novelty, best_sharpe=abs(sharpe or 0), best_fitness=abs(fitness or 0),
                                   pass_count=int(ok_gate), fail_count=int(not ok_gate))
                    db.add(row)
                else:
                    row.novelty=max(row.novelty, novelty); row.best_sharpe=max(row.best_sharpe, abs(sharpe or 0));
                    row.best_fitness=max(row.best_fitness, abs(fitness or 0)); row.pass_count += int(ok_gate); row.fail_count += int(not ok_gate)
                # Per-field empirical profile.
                for fid in dna.get("fields", []):
                    fi=db.scalar(select(M.FieldInsight).where(M.FieldInsight.field_id==fid, M.FieldInsight.region==region))
                    if not fi:
                        fi=M.FieldInsight(field_id=fid,region=region); db.add(fi)
                    fi.uses += 1; fi.valid_uses += 1; fi.last_at=time.time()
                    if sharpe is not None: fi.sum_sharpe += abs(sharpe)
                    if fitness is not None: fi.sum_fitness += abs(fitness)
                    if ok_gate: fi.passed_uses += 1
                    else: fi.failed_uses += 1
                    ops=set(json.loads(fi.successful_operators_json or "[]"))
                    if ok_gate: ops.update(_operators_in(expr))
                    fi.successful_operators_json=json.dumps(sorted(ops))
                if not ok_gate:
                    db.add(M.ResearchFailure(expression=expr, region=region, reason=(reasons[0] if reasons else "gate failure"),
                                             details_json=json.dumps({"reasons":reasons,"sharpe":sharpe,"fitness":fitness,"turnover":turn}), experiment_id=0))
                db.add(M.SimResult(alpha_id=aid or "", expression=expr, region=region, delay=delay,
                                   universe=settings.get("universe", ""), neutralization=settings.get("neutralization", ""),
                                   sharpe=sharpe, fitness=fitness, turnover=turn, returns=returns,
                                   margin=margin, drawdown=drawdown, self_corr=self_c, prod_corr=prod_c, powerpool_corr=pool_c,
                                   tests_failed=tests_failed, passed_gate=ok_gate, gate_reasons=json.dumps(reasons), n_ops=n_ops, tagged=tag_used, execution_key=execution_key, variant_id=int(variant_id or 0), experiment_id=int(experiment_id or 0), execution_config_json=json.dumps({"delay": delay, "universe": settings.get("universe", ""), "neutralization": settings.get("neutralization", ""), "decay": decay, "truncation": truncation, "test_period": test_period, "pasteurization": pasteurization, "unit_handling": unit_handling, "nan_handling": nan_handling, "max_trade": max_trade, "visualization": visualization, "gate_thresholds": {"sharpe": thr_sharpe, "fitness": thr_fit, "max_turnover": max_turnover, "max_corr": max_corr}})))
                db.commit()
        except Exception as mem_err:
            # Research memory must never make a BRAIN simulation fail. The core SimResult is still stored.
            with SessionLocal() as db:
                db.add(M.SimResult(alpha_id=aid or "", expression=expr, region=region, delay=delay,
                                   universe=settings.get("universe", ""), neutralization=settings.get("neutralization", ""),
                                   sharpe=sharpe, fitness=fitness, turnover=turn, returns=returns, margin=margin, drawdown=drawdown,
                                   self_corr=self_c, prod_corr=prod_c, powerpool_corr=pool_c, tests_failed=tests_failed,
                                   passed_gate=ok_gate, gate_reasons=json.dumps(reasons), n_ops=n_ops, tagged=tag_used, execution_key=execution_key, variant_id=int(variant_id or 0), experiment_id=int(experiment_id or 0), execution_config_json=json.dumps({"delay": delay, "universe": settings.get("universe", ""), "neutralization": settings.get("neutralization", ""), "decay": decay, "truncation": truncation, "test_period": test_period, "pasteurization": pasteurization, "unit_handling": unit_handling, "nan_handling": nan_handling, "max_trade": max_trade, "visualization": visualization, "gate_thresholds": {"sharpe": thr_sharpe, "fitness": thr_fit, "max_turnover": max_turnover, "max_corr": max_corr}})))
                db.commit()

        # Evolution simulations carry an exact variant_id + execution_key. Attach using that
        # immutable identity only. Never search by alpha_id, because the same BRAIN alpha id can
        # have multiple independently-tested configurations and concurrent jobs can finish out of order.
        if variant_id and execution_key:
            try:
                from app import evolution_service
                evolution_service.attach_simulation_to_variant(int(variant_id), execution_key)
            except Exception:
                # The SimResult is already durable. A later explicit attachment can reconcile the
                # lineage if the attachment transaction is temporarily unavailable.
                pass

        if fitness is not None and abs(fitness) > 0:
            try:
                # Credit the operators this alpha used, weighted by |fitness|, so the
                # diversity engine learns what actually WORKED (not just what was tried).
                _credit_usage(region, list(set(_operators_in(expr))), [], fitness)
            except Exception:
                pass

        if ok_gate:
            passed += 1
        rows.append({"alpha_id": aid, "expr": expr, "universe": settings.get("universe"),
                     "neutralization": settings.get("neutralization"), "sharpe": sharpe,
                     "fitness": fitness, "turnover": turn, "returns": returns, "margin": margin,
                     "drawdown": drawdown, "self_corr": self_c, "prod_corr": prod_c,
                     "powerpool_corr": pool_c, "tests_failed": tests_failed, "passed": ok_gate,
                     "reasons": reasons, "tag": tag_used})

    rows.sort(key=lambda x: (-(x["passed"]), -(abs(x["fitness"]) if x["fitness"] is not None else 0)))
    progress(log=f"done: {len(rows)} simulated · {passed} passed the gate · {tagged} tagged")
    return {"configs": len(alpha_list), "simulated": len(rows), "passed": passed, "tagged": tagged,
            "cancelled": cancelled, "errors": errors[:20], "thresholds": {"sharpe": thr_sharpe, "fitness": thr_fit},
            "results": rows}


def run_cross_region_sweep(*, expressions, regions, delay, instrument, neutralizations, decay,
                           truncation, concurrency, limit_of_multi, tag, winner_tag,
                           home_region="", progress, should_cancel) -> dict:
    """Run the same expressions across several regions to diversify fast. Each region uses a VALID
    universe/delay for it and that delay's gate thresholds.

    PRE-FLIGHT: before simulating, verify (against BRAIN's region-specific data-fields) that the
    dataset-specific datafields the expressions use actually EXIST in each region's universe. A
    region missing any required field is SKIPPED with the missing field names — so we never waste
    a simulation on a region that can't support the alpha, and the user sees exactly why."""
    out = []
    total = len(regions)

    # Which dataset-specific fields must exist everywhere? (Universal price/volume/group fields are
    # assumed present and not looked up.) Confirm them in the HOME region first — that's the ground
    # truth for "this identifier is a real datafield" vs a constant the search doesn't index.
    candidates = set()
    for e in expressions:
        candidates.update(_leaf_idents(e))
    to_check = {c for c in candidates if c.lower() not in _UNIVERSAL}
    required: set = set()
    if to_check and home_region:
        try:
            progress(message=f"verifying {len(to_check)} datafield(s) in {home_region}…")
            required = engine.datafields_present(home_region, delay, "TOP3000", instrument, to_check)
        except Exception:
            required = set()   # can't determine -> don't block, fall back to simulate-and-report

    for i, region in enumerate(regions):
        if should_cancel():
            break
        progress(total=total, done=i, message=f"sweeping {region} ({i + 1}/{total})…")
        _, d, universe = engine.valid_combo(instrument, region, delay, "TOP3000")

        if required:
            progress(message=f"checking data in {region}…")
            try:
                present = engine.datafields_present(region, d, universe, instrument, required)
            except Exception:
                present = set(required)   # lookup failed -> don't block this region
            missing = sorted(required - present)
            if missing:
                out.append({"region": region, "delay": d, "universe": universe,
                            "skipped": "fields not available in this region", "missing": missing})
                continue

        t = gate_thresholds(d)
        try:
            res = run_simulation(
                expressions=expressions, region=region, delay=d, universes=[universe],
                neutralizations=neutralizations or ["INDUSTRY"], decay=decay, truncation=truncation,
                test_period="P0Y", pasteurization="ON", unit_handling="VERIFY", nan_handling="OFF",
                max_trade="OFF", visualization=False, concurrency=concurrency, limit_of_multi=limit_of_multi,
                max_turnover=0.70, min_sharpe=t["sharpe"], min_fitness=t["fitness"], max_corr=0.70,
                tag=tag, winner_tag=winner_tag, winner_color="GREEN", tag_winners_above=1.0,
                check_submission=False, get_pnl=False, get_stats=False,
                progress=lambda **k: None, should_cancel=should_cancel)
            out.append({"region": region, "delay": d, "universe": universe,
                        "passed": res.get("passed", 0), "simulated": res.get("simulated", 0),
                        "results": (res.get("results") or [])[:15]})
        except Exception as e:  # noqa: BLE001
            out.append({"region": region, "error": str(e).splitlines()[0][:140]})
    progress(total=total, done=total, message="sweep complete")
    return {"regions": out, "checked_fields": sorted(required)}


def run_batch(*, batches, region, delay, universes, neutralizations, decay, truncation,
              concurrency, limit_of_multi, min_sharpe, min_fitness, tag, winner_tag,
              progress, should_cancel) -> dict:
    """Run several expression batches sequentially in one background job — queue them and let it
    work unattended. Each batch reports its own pass count."""
    out = []
    for i, b in enumerate(batches):
        if should_cancel():
            break
        exprs = [e.strip() for e in b.get("expressions", []) if e.strip()]
        progress(total=len(batches), done=i, message=f"batch {i + 1}/{len(batches)} · {b.get('label', '')} ({len(exprs)})")
        if not exprs:
            out.append({"label": b.get("label", ""), "skipped": "empty"})
            continue
        try:
            res = run_simulation(
                expressions=exprs, region=region, delay=delay, universes=universes or ["TOP3000"],
                neutralizations=neutralizations or ["INDUSTRY"], decay=decay, truncation=truncation,
                test_period="P0Y", pasteurization="ON", unit_handling="VERIFY", nan_handling="OFF",
                max_trade="OFF", visualization=False, concurrency=concurrency, limit_of_multi=limit_of_multi,
                max_turnover=0.70, min_sharpe=min_sharpe, min_fitness=min_fitness, max_corr=0.70,
                tag=tag, winner_tag=winner_tag, winner_color="GREEN", tag_winners_above=1.0,
                check_submission=False, get_pnl=False, get_stats=False,
                progress=lambda **k: None, should_cancel=should_cancel)
            out.append({"label": b.get("label", ""), "passed": res.get("passed", 0), "simulated": res.get("simulated", 0)})
        except Exception as e:  # noqa: BLE001
            out.append({"label": b.get("label", ""), "error": str(e).splitlines()[0][:140]})
    progress(total=len(batches), done=len(batches), message="queue complete")
    return {"batches": out}


def run_walk_forward(*, expressions, region, instrument, neutralizations, decay, truncation,
                     concurrency, limit_of_multi, tag, winner_tag, progress, should_cancel) -> dict:
    """Regime/robustness check: run the same expressions at BOTH delay 1 and delay 0 (each with a
    valid universe + that delay's gate). An alpha that holds up at both delays is far more likely
    to be real than overfit to one."""
    out = []
    delays = [1, 0]
    for i, d in enumerate(delays):
        if should_cancel():
            break
        progress(total=len(delays), done=i, message=f"walk-forward · delay {d}…")
        _, dd, universe = engine.valid_combo(instrument, region, d, "TOP3000")
        if dd != d:
            out.append({"delay": d, "skipped": f"delay {d} not available in {region}"})
            continue
        t = gate_thresholds(d)
        try:
            res = run_simulation(
                expressions=expressions, region=region, delay=d, universes=[universe],
                neutralizations=neutralizations or ["INDUSTRY"], decay=decay, truncation=truncation,
                test_period="P0Y", pasteurization="ON", unit_handling="VERIFY", nan_handling="OFF",
                max_trade="OFF", visualization=False, concurrency=concurrency, limit_of_multi=limit_of_multi,
                max_turnover=0.70, min_sharpe=t["sharpe"], min_fitness=t["fitness"], max_corr=0.70,
                tag=tag, winner_tag=winner_tag, winner_color="GREEN", tag_winners_above=1.0,
                check_submission=False, get_pnl=False, get_stats=False,
                progress=lambda **k: None, should_cancel=should_cancel)
            out.append({"delay": d, "universe": universe, "passed": res.get("passed", 0),
                        "simulated": res.get("simulated", 0)})
        except Exception as e:  # noqa: BLE001
            out.append({"delay": d, "error": str(e).splitlines()[0][:140]})
    return {"delays": out}


# ── stored results + success rate ────────────────────────────────────────────────────

def list_results(limit: int = 200) -> dict:
    with SessionLocal() as db:
        rows = db.scalars(select(M.SimResult).order_by(desc(M.SimResult.created_at)).limit(limit)).all()
        out = [{"alpha_id": r.alpha_id, "expr": r.expression, "region": r.region, "delay": r.delay,
                "universe": r.universe, "neutralization": r.neutralization, "sharpe": r.sharpe,
                "fitness": r.fitness, "turnover": r.turnover, "returns": r.returns,
                "margin": r.margin, "drawdown": r.drawdown, "self_corr": r.self_corr,
                "prod_corr": r.prod_corr, "powerpool_corr": r.powerpool_corr,
                "tests_failed": r.tests_failed, "passed": r.passed_gate,
                "reasons": json.loads(r.gate_reasons or "[]"), "tag": r.tagged} for r in rows]
        n_all = db.scalar(select(func.count()).select_from(M.SimResult)) or 0
        n_pass = db.scalar(select(func.count()).select_from(M.SimResult).where(M.SimResult.passed_gate.is_(True))) or 0
    return {"results": out, "total": n_all, "passed": n_pass,
            "success_rate": round(n_pass / n_all, 3) if n_all else 0.0}
