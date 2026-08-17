"""Submission Manager for ACE Studio.

This module deliberately separates simulation from submission. Passing the research/simulation
gate only makes an alpha a candidate. A user can queue candidates, see the local daily quota,
and explicitly submit them to BRAIN. The local quota is a safety guard, not a claim about
BRAIN's server-side allowance.
"""
from __future__ import annotations

import time
from datetime import datetime
from zoneinfo import ZoneInfo
import threading

_SUBMIT_LOCK = threading.Lock()

from sqlalchemy import select, func

from app.db.base import SessionLocal
from app.db import models as M


DEFAULT_LIMIT = 4
DEFAULT_TZ = "Africa/Lagos"


def _now() -> float:
    return time.time()


def _today(tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo(DEFAULT_TZ)
    return datetime.now(tz).date().isoformat()


def _settings(db):
    row = db.get(M.SubmissionSettings, 1)
    if not row:
        row = M.SubmissionSettings(id=1, daily_limit=DEFAULT_LIMIT, timezone=DEFAULT_TZ)
        db.add(row)
        db.commit()
    return row


def _record_dict(r):
    return {
        "id": r.id, "alpha_id": r.alpha_id, "sim_result_id": r.sim_result_id, "variant_id": r.variant_id, "execution_key": r.execution_key, "expression": r.expression,
        "region": r.region, "delay": r.delay, "universe": r.universe,
        "neutralization": r.neutralization, "sharpe": r.sharpe, "fitness": r.fitness,
        "turnover": r.turnover, "novelty": r.novelty, "robustness": r.robustness,
        "prod_corr": r.prod_corr, "status": r.status, "queued_for": r.queued_for,
        "submitted_at": r.submitted_at, "error": r.error, "notes": r.notes,
        "created_at": r.created_at, "updated_at": r.updated_at,
    }


def status():
    with SessionLocal() as db:
        st = _settings(db)
        today = _today(st.timezone)
        submitted_today = db.scalar(
            select(func.count()).select_from(M.SubmissionRecord).where(
                M.SubmissionRecord.status.in_(["submitted", "submitting"]),
                M.SubmissionRecord.queued_for == today,
            )
        ) or 0
        queued = db.scalar(
            select(func.count()).select_from(M.SubmissionRecord).where(
                M.SubmissionRecord.status == "queued"
            )
        ) or 0
        return {
            "date": today,
            "daily_limit": max(0, int(st.daily_limit)),
            "submitted_today": int(submitted_today),
            "remaining_today": max(0, int(st.daily_limit) - int(submitted_today)),
            "queued": int(queued),
            "timezone": st.timezone,
        }


def set_settings(daily_limit: int, timezone: str | None = None):
    limit = max(0, int(daily_limit))
    with SessionLocal() as db:
        st = _settings(db)
        st.daily_limit = limit
        if timezone:
            try:
                ZoneInfo(timezone)
                st.timezone = timezone
            except Exception:
                raise ValueError(f"Unknown timezone: {timezone}")
        st.updated_at = _now()
        db.commit()
    return status()


def list_queue(limit: int = 200):
    with SessionLocal() as db:
        rows = db.scalars(
            select(M.SubmissionRecord).order_by(
                (M.SubmissionRecord.status == "queued").desc(),
                M.SubmissionRecord.fitness.desc().nullslast(),
                M.SubmissionRecord.created_at.desc(),
            ).limit(max(1, min(limit, 1000)))
        ).all()
        return [_record_dict(r) for r in rows]


def _candidate_from_result(db, alpha_id: str = "", sim_result_id: int = 0):
    if sim_result_id:
        row = db.get(M.SimResult, int(sim_result_id))
    else:
        row = db.scalar(select(M.SimResult).where(M.SimResult.alpha_id == alpha_id).order_by(M.SimResult.created_at.desc()))
    if not row:
        return None, None
    dna = db.scalar(select(M.AlphaDNA).where(M.AlphaDNA.expression_key == __import__(
        "app.discovery.service", fromlist=["canonical"]).canonical(row.expression)
    ))
    return row, dna


def _readiness(row, dna):
    gaps = []
    if not row.alpha_id:
        gaps.append("missing BRAIN alpha id")
    if not row.passed_gate:
        gaps.append("did not pass the simulation gate")
    if row.prod_corr is None:
        gaps.append("production correlation has not been verified")
    elif abs(row.prod_corr) >= 0.70:
        gaps.append(f"production correlation {abs(row.prod_corr):.2f} ≥ 0.70")
    if row.self_corr is not None and abs(row.self_corr) >= 0.70:
        gaps.append(f"self-correlation {abs(row.self_corr):.2f} ≥ 0.70")
    if row.powerpool_corr is not None and abs(row.powerpool_corr) >= 0.70:
        gaps.append(f"power-pool correlation {abs(row.powerpool_corr):.2f} ≥ 0.70")
    return gaps


def queue_simulation(sim_result_id: int, notes: str = ""):
    with SessionLocal() as db:
        sim, dna = _candidate_from_result(db, sim_result_id=sim_result_id)
        if not sim: raise ValueError(f"No stored simulation result found for {sim_result_id}.")
        if not sim.passed_gate: raise ValueError("Only simulation results that passed the gate can be queued.")
        gaps = _readiness(sim, dna)
        if gaps: raise ValueError("Not ready for submission: " + "; ".join(gaps))
        existing=db.scalar(select(M.SubmissionRecord).where(M.SubmissionRecord.sim_result_id == int(sim.id)))
        if existing:
            if existing.status == "error":
                existing.status="queued"; existing.error=""; existing.updated_at=_now(); existing.notes=notes or existing.notes; db.commit()
            return _record_dict(existing)
        rec=M.SubmissionRecord(alpha_id=sim.alpha_id, sim_result_id=sim.id, variant_id=int(sim.variant_id or 0), execution_key=sim.execution_key or "",
            expression=sim.expression, region=sim.region, delay=sim.delay, universe=sim.universe, neutralization=sim.neutralization,
            sharpe=sim.sharpe, fitness=sim.fitness, turnover=sim.turnover, novelty=dna.novelty if dna else None,
            robustness=dna.robustness if dna else None, prod_corr=sim.prod_corr, status="queued",
            queued_for=_today(_settings(db).timezone), notes=notes or "")
        db.add(rec); db.commit(); return _record_dict(rec)


def queue_alpha(alpha_id: str, notes: str = ""):
    alpha_id=str(alpha_id or "").strip()
    if not alpha_id: raise ValueError("alpha_id is required")
    with SessionLocal() as db:
        sim=db.scalar(select(M.SimResult).where(M.SimResult.alpha_id==alpha_id, M.SimResult.passed_gate.is_(True)).order_by(M.SimResult.created_at.desc()))
        if not sim: raise ValueError(f"No passed simulation result found for {alpha_id}.")
        sid=sim.id
    return queue_simulation(sid, notes)


def remove_alpha(record_id: int):
    with SessionLocal() as db:
        row = db.get(M.SubmissionRecord, int(record_id))
        if not row:
            raise ValueError("submission record not found")
        if row.status == "submitted":
            raise ValueError("A submitted alpha cannot be removed from the history.")
        db.delete(row)
        db.commit()
    return {"ok": True}


def _submit_one(alpha_id: str):
    from app.brain import engine
    session = engine.require_session()
    return engine.ace.submit_alpha(session, alpha_id)


def submit_record(record_id: int):
    with _SUBMIT_LOCK:
        with SessionLocal() as db:
            rec=db.get(M.SubmissionRecord,int(record_id))
            if not rec: raise ValueError("submission record not found")
            if rec.status=="submitted": return _record_dict(rec)
            st=_settings(db); today=_today(st.timezone)
            submitted_today=db.scalar(select(func.count()).select_from(M.SubmissionRecord).where(
                M.SubmissionRecord.status.in_(["submitted","submitting"]), M.SubmissionRecord.queued_for==today)) or 0
            if submitted_today >= int(st.daily_limit):
                raise ValueError(f"Today's local submission quota is full ({st.daily_limit}). The alpha remains queued for the next day.")
            alpha_id=rec.alpha_id
            rec.status="submitting"; rec.error=""; rec.updated_at=_now(); rec.queued_for=today; db.commit()
    try:
        ok=bool(_submit_one(alpha_id))
    except Exception as e:
        with SessionLocal() as db:
            rec=db.get(M.SubmissionRecord,int(record_id))
            if rec: rec.status="error"; rec.error=str(e).splitlines()[0][:500]; rec.updated_at=_now(); db.commit()
        raise
    with SessionLocal() as db:
        rec=db.get(M.SubmissionRecord,int(record_id))
        if not rec: raise ValueError("submission record disappeared")
        if ok:
            rec.status="submitted"; rec.submitted_at=_now(); rec.error=""
        else:
            rec.status="error"; rec.error="BRAIN rejected the submission request."
        rec.updated_at=_now(); db.commit(); return _record_dict(rec)


def reset_error(record_id: int):
    with SessionLocal() as db:
        rec = db.get(M.SubmissionRecord, int(record_id))
        if not rec:
            raise ValueError("submission record not found")
        if rec.status == "submitted":
            raise ValueError("Submitted alpha cannot be reset.")
        rec.status = "queued"
        rec.error = ""
        rec.updated_at = _now()
        db.commit()
        return _record_dict(rec)


def candidates(limit: int = 200):
    """Return recent metric-passing results with their queue/readiness status."""
    with SessionLocal() as db:
        sims = db.scalars(
            select(M.SimResult).where(M.SimResult.passed_gate.is_(True))
            .order_by(M.SimResult.created_at.desc()).limit(max(1, min(limit, 1000)))
        ).all()
        out = []
        for r in sims:
            rec = db.scalar(select(M.SubmissionRecord).where(M.SubmissionRecord.sim_result_id == r.id))
            dna = db.scalar(select(M.AlphaDNA).where(M.AlphaDNA.expression_key == __import__(
                "app.discovery.service", fromlist=["canonical"]).canonical(r.expression)
            ))
            gaps = _readiness(r, dna)
            out.append({
                "alpha_id": r.alpha_id, "expr": r.expression, "region": r.region, "delay": r.delay,
                "universe": r.universe, "neutralization": r.neutralization,
                "sharpe": r.sharpe, "fitness": r.fitness, "turnover": r.turnover,
                "prod_corr": r.prod_corr, "self_corr": r.self_corr,
                "powerpool_corr": r.powerpool_corr, "passed": r.passed_gate,
                "novelty": dna.novelty if dna else None, "robustness": dna.robustness if dna else None,
                "readiness_gaps": gaps,
                "queue_status": rec.status if rec else "",
                "record_id": rec.id if rec else None,
            })
        return out


def rank_score(x):
    """A transparent queue score. Performance matters, but novelty and robustness get weight."""
    fit = abs(x.get("fitness") or 0)
    shp = abs(x.get("sharpe") or 0)
    novelty = x.get("novelty")
    robustness = x.get("robustness")
    turnover = x.get("turnover")
    prod = x.get("prod_corr")
    score = 0.50 * fit + 0.25 * min(shp, 4.0) / 2.0
    if novelty is not None:
        score += 0.15 * max(0.0, min(1.0, novelty))
    if robustness is not None:
        score += 0.10 * max(0.0, min(1.0, robustness))
    if turnover is not None and turnover > 0.50:
        score -= min(0.15, (turnover - 0.50) * 0.30)
    if prod is not None:
        score -= 0.10 * max(0.0, abs(prod))
    return round(score, 4)
