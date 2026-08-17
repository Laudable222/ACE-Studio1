"""Category-combination safety — encodes the WorldQuant "Combining Data Categories Safely"
guide so the LLM only pairs categories that reflect DIFFERENT economic mechanisms.

Core idea (from the guide): overfitting risk rises with mechanism OVERLAP, not dataset count.
Combine across the three pillars (Intrinsic Value / Expectations / Positioning); avoid pairs
that restate one mechanism. Note the guide's own correction: Analyst is NOT independent of
Earnings/Fundamental (built from the same reported financials), so those pairs are risky.
"""

from __future__ import annotations

# category id (lowercased) -> pillar
PILLAR = {
    "fundamental": "intrinsic_value", "earnings": "intrinsic_value",
    "analyst": "expectations", "sentiment": "expectations", "macro": "expectations",
    "option": "positioning", "short_interest": "positioning", "shortinterest": "positioning",
    "institutions": "positioning", "insiders": "positioning",
    "news": "context", "social_media": "context", "socialmedia": "context",
    "price_volume": "flow", "pricevolume": "flow", "imbalance": "flow",
    "risk": "other", "broker": "other", "model": "other", "other": "other",
}

# Safer -> riskier (guide's 1..17 ranking). Lower is safer/more independent on average.
RISK_RANK = {
    "fundamental": 1, "macro": 2, "option": 3, "short_interest": 4, "sentiment": 5,
    "analyst": 6, "earnings": 7, "institutions": 8, "insiders": 9, "news": 10, "risk": 11,
    "price_volume": 12, "imbalance": 13, "social_media": 14, "broker": 15, "model": 16, "other": 17,
}

# Explicitly endorsed "very low risk" pairs (guide section 3).
SAFE_PAIRS = {frozenset(p) for p in [
    ("fundamental", "sentiment"), ("fundamental", "macro"), ("fundamental", "option"),
    ("fundamental", "short_interest"), ("analyst", "macro"), ("analyst", "sentiment"),
    ("news", "fundamental"), ("news", "option"), ("institutions", "fundamental"),
    ("insiders", "fundamental"), ("earnings", "option"),
]}

# Pairs to AVOID even though the labels differ — same mechanism restated (incl. the guide's
# Analyst correction and the high-risk cousins).
AVOID_PAIRS = {frozenset(p) for p in [
    ("fundamental", "analyst"), ("earnings", "analyst"), ("fundamental", "earnings"),
    ("news", "social_media"), ("news", "sentiment"), ("social_media", "sentiment"),
    ("price_volume", "imbalance"), ("price_volume", "option"), ("imbalance", "option"),
]}


def _norm(c: str) -> str:
    return str(c or "").strip().lower().replace(" ", "_").replace("-", "_")


def pair_risk(a: str, b: str) -> str:
    """Classify a pair: 'safe' | 'avoid' | 'inspect'."""
    a, b = _norm(a), _norm(b)
    if a == b:
        return "avoid"
    fs = frozenset((a, b))
    if fs in SAFE_PAIRS:
        return "safe"
    if fs in AVOID_PAIRS or PILLAR.get(a, "x") == PILLAR.get(b, "y"):
        return "avoid"  # same pillar = same mechanism family
    return "inspect"


def best_two_category_pair(categories: list) -> tuple | None:
    """From the categories actually present, pick the safest pair that mixes DIFFERENT
    mechanisms — prefer an endorsed safe pair, else a different-pillar pair, ranked by the
    combined safety score. Returns (cat_a, cat_b) or None if nothing safe exists."""
    cats = sorted({_norm(c) for c in categories if _norm(c)})
    best, best_key = None, None
    for i in range(len(cats)):
        for j in range(i + 1, len(cats)):
            a, b = cats[i], cats[j]
            risk = pair_risk(a, b)
            if risk == "avoid":
                continue
            # safe (0) ranks ahead of inspect (1); then lower combined risk rank is better.
            key = (0 if risk == "safe" else 1,
                   RISK_RANK.get(a, 17) + RISK_RANK.get(b, 17))
            if best_key is None or key < best_key:
                best_key, best = key, (a, b)
    return best


def safest_partner(category: str, candidates: list) -> str | None:
    """Given a chosen category and the categories available, return the safest DIFFERENT-mechanism
    category to pair it with (endorsed-safe first, then lowest-risk different pillar), or None."""
    category = _norm(category)
    best, best_key = None, None
    for cand in candidates:
        cn = _norm(cand)
        if not cn or cn == category:
            continue
        risk = pair_risk(category, cn)
        if risk == "avoid":
            continue
        key = (0 if risk == "safe" else 1, RISK_RANK.get(cn, 17))
        if best_key is None or key < best_key:
            best_key, best = key, cn
    return best


def combination_guidance(mode: str, categories: list) -> str:
    """Prompt block describing how to combine categories for the chosen mode."""
    base = (
        "=== DATA-CATEGORY COMBINATION RULES (WorldQuant 'Combining Data Categories Safely') ===\n"
        "Overfitting risk rises with MECHANISM overlap, not dataset count. The three pillars are "
        "Intrinsic Value (fundamental, earnings), Expectations (analyst, sentiment, macro) and "
        "Positioning (option, short_interest, institutions, insiders). Combine ACROSS pillars; never "
        "restate one mechanism under two labels. Analyst is NOT independent of earnings/fundamental "
        "(built from the same financials) — do not pair them.\n")
    if mode != "multi_two_categories":
        return base + "For this run use a SINGLE category's fields.\n"
    pair = best_two_category_pair(categories)
    if pair:
        return base + (f"Use EXACTLY TWO fields from TWO DIFFERENT categories, and the SAFEST available "
                       f"pairing is: {pair[0]} + {pair[1]}. Use those two categories; leave riskier or "
                       "same-mechanism categories out.\n")
    return base + ("No sufficiently independent two-category pairing is available in the selected data — "
                   "fall back to SINGLE-field ideas rather than forcing a risky combination.\n")
