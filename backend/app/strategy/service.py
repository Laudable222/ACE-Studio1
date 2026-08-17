"""Strategy Atlas — LLM exploration of the strategy space per data category.

Never hardcodes strategies: for a category/region it asks the model to explore, seeded by
the device so two users get DIFFERENT strategy sets. Each strategy carries a thesis and a
concrete way to build it, and can be pushed to the Research Lab or Generation.
"""

from __future__ import annotations

from app.brain import engine  # noqa: F401 — loads the vendored engine
from app.core.device import device_seed
from app.knowledge import service as knowledge

import llm_providers as L  # noqa: E402
import keys as keymgr       # noqa: E402

DELIM = "|||"


def _op_ref() -> str:
    """Operator reference for the strategy prompt (best-effort — never block exploration)."""
    try:
        from app.generation import service as gen
        from app.operators import service as opsvc
        return gen.operator_summary() + "\n" + opsvc.reference_block("REGULAR")
    except Exception:
        return "(operator list unavailable — use only standard BRAIN operators)"


def explore(category: str, region: str, delay: int, instrument: str, n: int, mode: str, progress) -> dict:
    chain = __import__("app.core.llm_router", fromlist=["get_chain"]).get_chain("research")
    if not chain:
        raise RuntimeError("No LLM providers set up. Add an API key in Settings.")
    seed = device_seed()
    d0 = ("For delay 0, weight strategies whose edge is a strong risk-adjusted return (Sharpe).\n"
          if str(delay) == "0" else "")

    from app.knowledge import categories as catlib

    # In two-category mode, pick the SAFEST different-mechanism partner category (per the BRAIN
    # combining-safely guide) from what's catalogued, and explore strategies that pair them.
    partner = None
    ds = knowledge.datasets_in_category(category, region)
    if mode == "two_categories":
        available = [c["category"] for c in knowledge.overview().get("categories", [])]
        partner = catlib.safest_partner(category, available)
        if partner:
            ds = ds + knowledge.datasets_in_category(partner, region)

    if ds and partner:
        by_cat = {category: knowledge.datasets_in_category(category, region),
                  partner: knowledge.datasets_in_category(partner, region)}
        blocks = []
        for cat, items in by_cat.items():
            lines = "\n".join(f"  - {d['name'] or d['id']}: {(d['description'] or '')[:70]}" for d in items[:60])
            blocks.append(f"=== DATASETS IN {cat.upper()} ===\n{lines}")
        ds_rule = (
            f"TWO-CATEGORY mode. Combine EXACTLY TWO fields from TWO DIFFERENT categories — {category} and "
            f"{partner} — chosen because they reflect DIFFERENT economic mechanisms (lowest overfitting risk "
            "per the BRAIN combining-safely guide). Each strategy uses one field from each category and names "
            "BOTH datasets it relies on.\n" + "\n".join(blocks) + "\n")
    elif ds:
        ds_block = "\n".join(f"  - {d['name'] or d['id']}: {(d['description'] or '')[:80]}" for d in ds[:120])
        ds_rule = (f"This category has {len(ds)} datasets (listed below). Explore the WHOLE category: your "
                   "strategies must COLLECTIVELY draw on the range of these datasets, not just the obvious "
                   "one, and each should say which dataset(s) it uses.\n"
                   f"=== DATASETS IN {category.upper()} ===\n{ds_block}\n")
    else:
        ds_rule = "No datasets are catalogued for this category yet — reason from the category name.\n"

    ds_names = [d["name"] or d["id"] for d in ds]
    meta = (
        "You are a senior quant strategist mapping the strategy space for a WorldQuant BRAIN data "
        "category. Produce DEEP, specific, immediately-actionable strategies — not generic textbook lines.\n"
        f"Category: {category}. Region: {region}, delay {delay}, {instrument}.\n"
        f"Exploration seed: {seed} — let it push you toward LESS obvious, more varied angles; be original.\n"
        + d0 + ds_rule +
        f"Propose {n} DISTINCT strategy archetypes for this category and region. Each must exploit a "
        "DIFFERENT market mechanism, name the SPECIFIC dataset(s) from the list it relies on, and reason "
        "about what actually drives THIS region (liquidity, microstructure, how information diffuses). "
        "Avoid overfitting-prone combinations.\n"
        f"=== OPERATORS (reference the correct ones; pass named params as keywords) ===\n"
        f"{_op_ref()}\n"
        f"Return ONLY a JSON array of {n} strings, each in this EXACT delimited form:\n"
        f"  name {DELIM} economic thesis: the mechanism, why it predicts returns, why it persists rather "
        f"than being arbitraged, and the expected sign + horizon (region-aware, 2-4 sentences) {DELIM} "
        f"how to build it: representative datafields, the operator families to use, neutralization/decay "
        f"hints, and one concrete example expression {DELIM} datasets: the EXACT dataset name(s) from the "
        f"list above that this strategy needs (comma-separated)\n")
    progress(message=f"exploring {category} across {len(ds)} dataset(s)…")
    res = __import__("app.core.llm_router", fromlist=["TaskLLM"]).TaskLLM("research").generate_list(meta, n=n)

    def match_datasets(raw: str) -> list:
        # Return the catalogued {id, name} pairs the text refers to (by name OR id), so the user
        # sees the exact dataset id to fetch.
        low = raw.lower()
        seen, out = set(), []
        for d in ds:
            nm = (d["name"] or "").lower()
            did = (d["id"] or "").lower()
            if d["id"] not in seen and ((nm and nm in low) or (did and did in low)):
                seen.add(d["id"]); out.append({"id": d["id"], "name": d["name"]})
        return out

    strategies = []
    for s in res.expressions:
        parts = [p.strip() for p in str(s).split(DELIM)]
        raw_ds = parts[3] if len(parts) > 3 else ""
        strategies.append({"name": parts[0] if parts else str(s),
                           "thesis": parts[1] if len(parts) > 1 else "",
                           "build": parts[2] if len(parts) > 2 else "",
                           "datasets": match_datasets(raw_ds) or match_datasets(parts[2] if len(parts) > 2 else "")})
    return {"category": category, "region": region, "partner": partner, "strategies": strategies,
            "datasets_explored": len(ds), "provider": res.provider, "model": res.model}
