"""Region & Universe Atlas — market characteristics per region, the universes/delays
available, how much the knowledge DB has learned per region, and cross-region transfer.
Delay-0 is called out because delay-0 alphas are judged on Sharpe."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from app.brain import engine
from app.db.base import SessionLocal
from app.db import models as M

PREFIX = "/api/regions"
router = APIRouter()

# Light, hand-written market notes so the atlas is useful even before an LLM pass. These
# are guidance, not hard rules — research should still reason from the data.
NOTES = {
    "USA": "Deep, liquid large-cap market; rich fundamental & news coverage; delay-1 is the norm, delay-0 is competitive and Sharpe-driven.",
    "GLB": "Global cross-section; strong for breadth and regionally-neutral ideas; watch currency and coverage differences.",
    "EUR": "Fragmented across exchanges/currencies; sector rotation and cross-listing effects matter; liquidity thinner than USA.",
    "ASI": "Asia ex-Japan; retail-heavy flow, faster reversals, microstructure and liquidity gating are important.",
    "CHN": "China A-shares; strong retail participation and momentum/reversal regimes; policy and liquidity sensitive.",
    "JPN": "Deep, developed market; sector and value/quality effects; disciplined risk model.",
    "KOR": "Liquid but retail-driven; fast information diffusion and reversals.",
    "IND": "Growing, retail-heavy; liquidity concentrated in large caps; momentum and event effects.",
    "HKG": "Cross-listing with China exposure; liquidity varies; sensitive to flows.",
    "TWN": "Tech-heavy, export-sensitive; liquidity concentrated; momentum regimes.",
    "AMR": "Americas ex-USA; thinner coverage; be careful with breadth and data availability.",
    "MEA": "Middle East / Africa; thinner liquidity and coverage; favour robust, simple signals.",
}


@router.get("/info")
def info():
    opts = engine.get_options().get("records", [])
    # region -> {delays, universes, neutralizations}
    reg: dict = {}
    for r in opts:
        e = reg.setdefault(r["region"], {"delays": set(), "universes": set(), "neutralizations": set()})
        e["delays"].add(r["delay"])
        e["universes"].update(r.get("universes", []))
        e["neutralizations"].update(r.get("neutralizations", []))

    with SessionLocal() as db:
        ds = dict(db.execute(select(M.Dataset.region, func.count()).group_by(M.Dataset.region)).all())
        fl = dict(db.execute(select(M.Field.region, func.count()).group_by(M.Field.region)).all())
        # cross-region concepts a region shares (by shared dataset id) with others
        by_id = db.execute(
            select(M.Dataset.dataset_id, func.count(func.distinct(M.Dataset.region)).label("n"))
            .group_by(M.Dataset.dataset_id)).all()
        shared_ids = {d for d, n in by_id if n > 1}
        region_shared: dict = {}
        if shared_ids:
            rows = db.execute(select(M.Dataset.region, M.Dataset.dataset_id).distinct()).all()
            for region_name, did in rows:
                if did in shared_ids:
                    region_shared[region_name] = region_shared.get(region_name, 0) + 1

    out = []
    for name, e in sorted(reg.items()):
        out.append({
            "region": name,
            "delays": sorted(e["delays"]),
            "universes": sorted(e["universes"]),
            "neutralizations": sorted(e["neutralizations"]),
            "datasets_known": int(ds.get(name, 0)),
            "fields_known": int(fl.get(name, 0)),
            "cross_region_datasets": int(region_shared.get(name, 0)),
            "delay0": 0 in e["delays"],
            "note": NOTES.get(name, "Reason from the data: check coverage, liquidity and delay conventions for this region."),
        })
    return {"regions": out}
