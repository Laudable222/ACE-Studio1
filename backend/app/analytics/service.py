"""Analytics — the studio's read side.

Turns the stored simulation results into the numbers the Command Center, Results and
Portfolio screens need: the rolling success rate, per-region/delay breakdowns, which
operators actually produce fitness (parsed from the winning expressions), a diversity
score, and the pairwise PnL correlation → largest mutually-uncorrelated set that tells a
user which alphas they can submit together (they submit on BRAIN themselves — the studio
never submits).
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select, desc

from app.brain import engine
from app.db.base import SessionLocal
from app.db import models as M
from app.generation.service import _operators_in

import ace_lib as ace  # noqa: E402


# ── summary / insights ───────────────────────────────────────────────────────────────

def summary() -> dict:
    with SessionLocal() as db:
        n_all = db.scalar(select(func.count()).select_from(M.SimResult)) or 0
        n_pass = db.scalar(select(func.count()).select_from(M.SimResult).where(M.SimResult.passed_gate.is_(True))) or 0
        by_region = db.execute(
            select(M.SimResult.region, func.count(), func.sum(func.cast(M.SimResult.passed_gate, __import__("sqlalchemy").Integer)))
            .group_by(M.SimResult.region)).all()
        by_delay = db.execute(
            select(M.SimResult.delay, func.count()).group_by(M.SimResult.delay)).all()
        best = db.scalars(select(M.SimResult).where(M.SimResult.passed_gate.is_(True))
                          .order_by(desc(func.abs(M.SimResult.fitness))).limit(8)).all()
        rows = db.scalars(select(M.SimResult)).all()

    # operator fitness — parsed from the actual expressions, averaged over |fitness|
    agg = defaultdict(lambda: {"n": 0, "sum": 0.0})
    regions_used = set()
    for r in rows:
        regions_used.add(r.region)
        if r.fitness is None:
            continue
        for op in set(_operators_in(r.expression or "")):
            agg[op]["n"] += 1
            agg[op]["sum"] += abs(r.fitness)
    insights = [{"operator": k, "count": v["n"], "avg_fitness": round(v["sum"] / v["n"], 4)}
                for k, v in agg.items() if v["n"] >= 2]
    insights.sort(key=lambda x: -x["avg_fitness"])

    return {
        "total": n_all, "passed": n_pass,
        "success_rate": round(n_pass / n_all, 3) if n_all else 0.0,
        "by_region": [{"region": r, "count": c, "passed": int(p or 0)} for r, c, p in by_region],
        "by_delay": [{"delay": d, "count": c} for d, c in by_delay],
        "best": [{"alpha_id": b.alpha_id, "expr": b.expression, "sharpe": b.sharpe,
                  "fitness": b.fitness, "region": b.region, "delay": b.delay} for b in best],
        "operator_insights": insights,
        "diversity": {"operators": len(agg), "regions": len(regions_used)},
    }


# ── pairwise correlation + max uncorrelated set ──────────────────────────────────────

def _max_cliques(nodes, edges):
    """All maximal cliques (Bron–Kerbosch with pivoting). An edge = 'uncorrelated enough
    to submit together', so a clique is a mutually-submittable set. Inlined so no networkx
    dependency is needed."""
    cliques = []

    def expand(r, p, x):
        if not p and not x:
            if r:
                cliques.append(sorted(r))
            return
        pivot = max(p | x, key=lambda n: len(edges.get(n, ())))
        for v in list(p - edges.get(pivot, set())):
            nb = edges.get(v, set())
            expand(r | {v}, p & nb, x & nb)
            p.discard(v)
            x.add(v)

    expand(set(), set(nodes), set())
    return cliques


def correlation(alpha_ids: list, threshold: float = 0.7, years: int = 4, progress=None) -> dict:
    import pandas as pd
    s = engine.require_session()
    ids = list(dict.fromkeys(a.strip() for a in alpha_ids if a.strip()))
    if len(ids) < 2:
        raise RuntimeError("Give at least two alpha ids to correlate.")
    frames, missing = [], []
    for i, aid in enumerate(ids):
        if progress:
            progress(message=f"PnL {i + 1}/{len(ids)}", total=len(ids), done=i)
        try:
            df = ace.get_alpha_daily_pnl(s, aid)
            if df is None or getattr(df, "empty", True):
                missing.append(aid)
            else:
                frames.append(df)
        except Exception:
            missing.append(aid)
    if len(frames) < 2:
        raise RuntimeError(f"Could not download PnL for at least two alphas ({len(missing)} failed).")

    pnl = pd.concat(frames).reset_index()
    pivot = pnl.pivot_table(index="date", columns="alpha_id", values="pnl")
    pivot = pivot.sort_values(by="date").tail(252 * max(1, years))   # BRAIN correlates over ~4y
    corr = pivot.corr()
    cols = [str(c) for c in corr.columns]

    matrix = [[None if pd.isna(corr.iloc[i, j]) else round(float(corr.iloc[i, j]), 3)
               for j in range(len(cols))] for i in range(len(cols))]
    edges = {c: set() for c in cols}
    pairs = []
    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            v = corr.loc[a, b]
            if pd.isna(v):
                continue
            if float(v) < threshold:
                edges[a].add(b); edges[b].add(a)
            else:
                pairs.append({"a": a, "b": b, "correlation": round(float(v), 3)})

    cliques = _max_cliques(cols, edges)
    for c in cols:
        if not edges[c] and [c] not in cliques:
            cliques.append([c])
    cliques.sort(key=len, reverse=True)
    return {"alpha_ids": cols, "threshold": threshold, "days": int(len(pivot)),
            "matrix": matrix, "correlated_pairs": sorted(pairs, key=lambda p: -p["correlation"]),
            "clusters": cliques[:20], "best_set": cliques[0] if cliques else [], "missing": missing}


def prod_corr_check(alpha_ids: list, threshold: float = 0.7, progress=None) -> dict:
    """Run BRAIN's PRODUCTION-correlation test for each alpha. This is the true submission gate:
    an alpha is submittable only if its prod-corr is below the threshold. BRAIN accepts ONE
    submission at a time, so this is judged PER ALPHA — not as a group. The measured value is
    written back onto the stored result so the Results screen's verdict becomes authoritative."""
    ids = list(dict.fromkeys(a.strip() for a in alpha_ids if a.strip()))
    if not ids:
        raise RuntimeError("Give at least one alpha id.")
    out = []
    for i, aid in enumerate(ids):
        if progress:
            progress(message=f"prod-corr {i + 1}/{len(ids)} · {aid}", total=len(ids), done=i)
        r = engine.prod_corr(aid, threshold)
        out.append(r)
        val = r.get("value")
        if val is not None:
            # Persist onto the most recent stored result for this alpha so its verdict updates.
            with SessionLocal() as db:
                row = db.scalar(select(M.SimResult).where(M.SimResult.alpha_id == aid)
                                .order_by(desc(M.SimResult.created_at)))
                if row is not None:
                    row.prod_corr = float(val)
                    db.commit()
    submittable = [r["alpha_id"] for r in out if r.get("submittable")]
    return {"threshold": threshold, "results": out, "submittable": submittable,
            "n_submittable": len(submittable)}


def experiment_ledger() -> dict:
    """Multiple-testing tracker: how many alphas have been simulated per region (and per dataset),
    so a result found only after a wide sweep can be treated with suspicion."""
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(M.SimResult)) or 0
        by_region = db.execute(
            select(M.SimResult.region, func.count()).group_by(M.SimResult.region)).all()
        # variants per dataset (from the usage table, which credits fields/datasets on simulate)
        ds = db.execute(
            select(M.Usage.key, func.sum(M.Usage.count)).where(M.Usage.kind == "dataset")
            .group_by(M.Usage.key)).all()
    top_ds = sorted([{"dataset": k, "variants": int(v or 0)} for k, v in ds],
                    key=lambda x: -x["variants"])[:12]
    return {"total_simulated": total,
            "by_region": [{"region": r or "?", "simulated": n} for r, n in by_region],
            "by_dataset": top_ds}


def passed_alpha_ids(limit: int = 40) -> list:
    with SessionLocal() as db:
        rows = db.scalars(select(M.SimResult.alpha_id)
                          .where(M.SimResult.passed_gate.is_(True), M.SimResult.alpha_id != "")
                          .order_by(desc(M.SimResult.created_at)).limit(limit)).all()
        return list(dict.fromkeys(rows))
