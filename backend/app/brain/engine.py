"""Brain engine adapter.

Reuses the battle-tested engine that already lives in `studio/` (ace_lib,
session_manager, wqb_data, keys) rather than duplicating ~2000 lines, and rebuilds only
the thin caching / session / retry layer the v2 HTTP modules need. The shared session
lives in `<repo>/session.pkl`, so v2 uses whatever session the user already has.

Everything here is import-safe: no network call happens at import time, and the engine
never triggers an interactive login (a headless server must fail cleanly instead).
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from app.core.config import get_settings

_settings = get_settings()
# The engine (ace_lib, session_manager, wqb_data, keys, llm_providers, wqb_llm, validator,
# …) is VENDORED under backend/vendor so v2 is fully self-contained and no longer needs the
# studio/ folder. engine.py is at backend/app/brain/engine.py → parents[2] == backend.
_VENDOR = Path(__file__).resolve().parents[2] / "vendor"

# Point the engine at the shared session BEFORE importing session_manager (it reads the
# path at import time), and make the vendored engine importable as top-level modules.
os.environ.setdefault("ACE_SESSION_FILE", str(_settings.repo_root / "session.pkl"))
if str(_VENDOR) not in sys.path:
    sys.path.insert(0, str(_VENDOR))

import ace_lib as ace            # noqa: E402
import session_manager as sm     # noqa: E402
import wqb_data                  # noqa: E402
import keys as keymgr            # noqa: E402


def _no_interactive_login(*_a, **_k):
    raise RuntimeError("BRAIN session expired. Log in again from Settings, then retry.")


# In a headless server we must never fall into ace.start_session()'s input()/biometrics.
ace.start_session = _no_interactive_login
keymgr.load_into_env()


class SessionExpired(Exception):
    """Raised when an operation needs a session and none is valid."""


_lock = threading.Lock()
_cache: dict = {"operators": None, "fields": {}, "options": None}
_OPTIONS_FILE = _settings.data_dir / "wqb_options.json"


# ── session ──────────────────────────────────────────────────────────────────────────

def session_status() -> dict:
    """Lenient status for the UI: {ok, state, remaining, remaining_human}."""
    u = sm.ui_session()
    return {**u, "status": sm.status_line(),
            "remaining_human": sm.format_time(u.get("remaining", 0))}


def session_restart() -> dict:
    with _lock:
        _cache["operators"] = None
        _cache["fields"].clear()
        _cache["options"] = None
    return sm.soft_reset()


def session_or_none():
    try:
        return sm.get_session(login=False)
    except sm.SessionExpired:
        return None


def require_session():
    s = session_or_none()
    if s is None:
        raise SessionExpired("No active BRAIN session. Log in from Settings, then retry.")
    return s


# ── retry wrapper ────────────────────────────────────────────────────────────────────

def brain_call(what: str, fn, *args, tries: int = 3, **kwargs):
    """Call a BRAIN data function with a few retries; surface a clean message on failure
    instead of a raw pandas/requests traceback."""
    last = None
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < tries - 1:
                time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"Could not load {what} from BRAIN — it returned a transient error "
                       f"({last}). Please try again in a moment.")


# ── options (instrument / region / delay / universe / neutralization) ────────────────

def get_options(refresh: bool = False) -> dict:
    if not refresh and _cache["options"] is not None:
        return _cache["options"]
    if not refresh and _OPTIONS_FILE.exists():
        try:
            import json
            _cache["options"] = json.loads(_OPTIONS_FILE.read_text())
            return _cache["options"]
        except Exception:
            pass
    s = require_session()
    df = brain_call("options", ace.get_instrument_type_region_delay, s)
    records = []
    for _, r in df.iterrows():
        records.append({
            "instrument": str(r["InstrumentType"]),
            "region": str(r["Region"]),
            "delay": int(r["Delay"]),
            "universes": [str(u) for u in (r["Universe"] or [])],
            "neutralizations": [str(n) for n in (r["Neutralization"] or [])],
        })
    data = {"records": records}
    try:
        import json
        _OPTIONS_FILE.write_text(json.dumps(data, indent=2))
    except Exception:
        pass
    _cache["options"] = data
    return data


def valid_combo(instrument_type: str, region: str, delay, universe: str):
    """Correct (delay, universe) to a combination that actually exists for this instrument+region,
    using the fetched options. So a fetch works even when the selected universe/delay isn't valid
    for the region (e.g. JPN doesn't have TOP3000) — we snap to a real one instead of erroring."""
    try:
        delay = int(delay)
    except (TypeError, ValueError):
        delay = 1
    try:
        recs = [r for r in get_options().get("records", [])
                if r["instrument"] == instrument_type and r["region"] == region]
    except Exception:
        return region, delay, universe
    if not recs:
        return region, delay, universe            # unknown region — let the call try as-is
    delays = sorted({r["delay"] for r in recs})
    if delay not in delays:
        delay = delays[0]
    universes = [u for r in recs if r["delay"] == delay for u in r["universes"]]
    if universe not in universes and universes:
        universe = universes[0]
    return region, delay, universe


# ── operators (cached) ───────────────────────────────────────────────────────────────

def operators_df():
    if _cache["operators"] is None:
        s = require_session()
        _cache["operators"] = brain_call("operators", ace.get_operators, s)
    return _cache["operators"]


# ── datasets ─────────────────────────────────────────────────────────────────────────

def get_datasets(region="USA", universe="TOP3000", delay=1, instrument_type="EQUITY",
                 theme=False, coverage_min=0.0, value_min=0.0, category="") -> list:
    import re
    import pandas as pd
    s = require_session()
    # Snap to a valid delay/universe for this region so the fetch doesn't fail on a mismatch.
    region, delay, universe = valid_combo(instrument_type, region, delay, universe)
    try:
        df = brain_call("datasets", ace.get_datasets, s, instrument_type=instrument_type,
                        region=region, delay=delay, universe=universe, theme=theme)
    except RuntimeError as e:
        # BRAIN returns nothing for a region/combo with no datasets -> pandas 'No objects to
        # concatenate'. That's an empty result, not a server error.
        if "No objects to concatenate" in str(e) or "concatenate" in str(e):
            return []
        raise
    if df is None or df.empty:
        return []
    # Ensure category_id/category_name are populated regardless of how BRAIN shaped the field. The
    # generic dict-expander only makes category_id when the FIRST row's category is a dict (a null
    # first row drops the whole column, and it can leave NaN on other rows), so when the raw
    # `category` object column is present we ALWAYS re-derive from it.
    def _cat(c, key):
        if isinstance(c, dict):
            return str(c.get(key) or "")
        return str(c) if (key == "id" and c is not None and str(c) != "nan") else ""
    if "category" in df.columns:
        df["category_id"] = df["category"].apply(lambda c: _cat(c, "id"))
        df["category_name"] = df["category"].apply(lambda c: _cat(c, "name"))
    else:
        if "category_id" not in df.columns:
            src = next((c for c in ("categoryId", "category.id", "categoryName", "category.name") if c in df.columns), None)
            if src:
                df["category_id"] = df[src].astype(str)
    if "coverage" in df.columns and coverage_min > 0:
        df = df[pd.to_numeric(df["coverage"], errors="coerce").fillna(0) >= coverage_min]
    if "valueScore" in df.columns and value_min > 0:
        df = df[pd.to_numeric(df["valueScore"], errors="coerce").fillna(0) >= value_min]
    if category:
        cats = {c.strip().lower() for c in re.split(r"[,\s]+", category) if c.strip()}
        cat_cols = [c for c in ("category_id", "category_name", "category",
                                "subcategory_id", "subcategory_name") if c in df.columns]
        if cats and cat_cols:
            mask = None
            for col in cat_cols:
                m = df[col].astype(str).str.lower().isin(cats)
                mask = m if mask is None else (mask | m)
            df = df[mask]
    if "valueScore" in df.columns:
        df = df.sort_values("valueScore", ascending=False)
    cols = [c for c in ["id", "name", "description", "coverage", "valueScore", "alphaCount",
                        "delay", "category_id", "category_name"] if c in df.columns]
    return _json_safe(df[cols].to_dict(orient="records"))


def datafields_present(region, delay, universe, instrument_type, ids, data_type="ALL") -> set:
    """Which of `ids` are REAL datafields in this region+universe, confirmed by BRAIN's
    region-specific /data-fields search (exact id match). Used by the cross-region sweep to
    verify data exists BEFORE simulating, instead of letting a doomed simulation fail. Snaps to a
    valid universe/delay for the region first, so the check uses the same combo the sim will."""
    want = {str(i).strip() for i in ids if str(i).strip()}
    if not want:
        return set()
    s = require_session()
    region, delay, universe = valid_combo(instrument_type, region, delay, universe)
    found = set()
    for fid in want:
        try:
            df = brain_call(f"datafield {fid}", ace.get_datafields, s,
                            instrument_type=instrument_type, region=region, delay=delay,
                            universe=universe, data_type=data_type, search=fid)
        except Exception:
            continue
        if df is not None and not getattr(df, "empty", True) and "id" in df.columns:
            if (df["id"].astype(str) == fid).any():
                found.add(fid)
    return found


# ── datafields (cached, deduped) ─────────────────────────────────────────────────────

def _dedup_fields(df):
    if df is None or getattr(df, "empty", True) or "id" not in df.columns:
        return df
    ids = df["id"].astype(str).str.strip()
    df = df[ids.ne("") & ids.ne("nan") & df["id"].notna()]
    subset=["id"]
    if "dataset_id" in df.columns:
        subset.append("dataset_id")
    return df.drop_duplicates(subset=subset, keep="first").reset_index(drop=True)


def _fields_key(ids, region, universe, delay, itype, data_type, search):
    return f"{itype}|{region}|{universe}|{delay}|{data_type}|{search}|{','.join(sorted(ids))}"


def fetch_fields(dataset_ids, region="USA", universe="TOP3000", delay=1,
                 instrument_type="EQUITY", data_type="ALL", search=""):
    import pandas as pd
    if not dataset_ids:
        return pd.DataFrame(), 0
    # Snap to a valid delay/universe for the region so a mismatch doesn't fail the fetch.
    region, delay, universe = valid_combo(instrument_type, region, delay, universe)
    key = _fields_key(dataset_ids, region, universe, delay, instrument_type, data_type, search)
    if key in _cache["fields"]:
        df = _cache["fields"][key]
        return df, int(df.attrs.get("raw_count", len(df)))
    s = require_session()
    try:
        df = brain_call("datafields", wqb_data.fetch_datafields, dataset_ids, s=s,
                        instrument_type=instrument_type, region=region, delay=delay,
                        universe=universe, data_type=data_type, search=search)
    except RuntimeError as e:
        if "concatenate" in str(e):
            return pd.DataFrame(), 0
        raise
    raw_n = 0 if (df is None or getattr(df, "empty", True)) else len(df)
    df = _dedup_fields(df)
    try:
        df.attrs["raw_count"] = raw_n
    except Exception:
        pass
    _cache["fields"][key] = df
    return df, raw_n


# ── alpha PnL / yearly stats (for the Results equity curve) ──────────────────────────

def alpha_pnl(alpha_id: str) -> dict:
    """Cumulative-PnL series for an alpha, plus max drawdown, for the equity curve."""
    s = require_session()
    try:
        df = brain_call("alpha PnL", ace.get_alpha_pnl, s, alpha_id)
    except Exception:
        return {"points": [], "max_drawdown": 0.0}
    if df is None or getattr(df, "empty", True):
        return {"points": [], "max_drawdown": 0.0}
    cols = list(df.columns)
    pnl_col = next((c for c in cols if str(c).lower() == "pnl"), None) or \
        next((c for c in cols if "pnl" in str(c).lower()), cols[-1])
    date_col = next((c for c in cols if "date" in str(c).lower()), cols[0])
    pts, peak, dd = [], -1e18, 0.0
    for _, r in df.iterrows():
        try:
            v = float(r[pnl_col])
        except (TypeError, ValueError):
            continue
        pts.append({"date": str(r[date_col])[:10], "pnl": round(v, 2)})
        peak = max(peak, v)
        dd = min(dd, v - peak)
    return {"points": pts, "max_drawdown": round(dd, 2)}


def prod_corr(alpha_id: str, threshold: float = 0.7) -> dict:
    """The alpha's PRODUCTION correlation — its max correlation against the production/OS pool.
    This is the real submission gate: an alpha is only submittable when this is below the
    threshold (0.70). Returns {value, result, submittable}. value is None when BRAIN has no
    prod-corr yet (e.g. the alpha isn't finished computing)."""
    s = require_session()
    try:
        df = brain_call("prod correlation", ace.check_prod_corr_test, s, alpha_id, threshold)
    except Exception as e:  # noqa: BLE001
        return {"alpha_id": alpha_id, "value": None, "result": "ERROR",
                "submittable": False, "error": str(e).splitlines()[0][:160]}
    if df is None or getattr(df, "empty", True):
        return {"alpha_id": alpha_id, "value": None, "result": "NONE", "submittable": False}
    row = df.iloc[0].to_dict()
    val = _json_safe(row.get("value"))
    res = str(row.get("result") or "")
    return {"alpha_id": alpha_id, "value": val, "result": res,
            "threshold": threshold, "submittable": (res == "PASS")}


def alpha_yearly(alpha_id: str) -> list:
    """Per-year statistics for an alpha (Sharpe/returns/turnover by year)."""
    s = require_session()
    try:
        df = brain_call("yearly stats", ace.get_alpha_yearly_stats, s, alpha_id)
    except Exception:
        return []
    if df is None or getattr(df, "empty", True):
        return []
    return _json_safe(df.to_dict(orient="records"))


# ── json safety ──────────────────────────────────────────────────────────────────────

def _json_safe(x):
    import math
    if isinstance(x, float):
        return None if (math.isnan(x) or math.isinf(x)) else x
    if isinstance(x, dict):
        return {k: _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, (str, bytes, bool, int)) or x is None:
        return x
    if hasattr(x, "item"):
        try:
            return _json_safe(x.item())
        except Exception:
            return str(x)
    return x
