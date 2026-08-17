"""
wqb_data.py — Enhanced DataField Fetching and Analysis for WorldQuant BRAIN.

Key enhancements over baseline:
  • fetch_datafields preserves ALL columns returned by ace.get_datafields() and
    calls _enrich_datafields() to add computed helper columns (competition_tier,
    coverage_tier, is_virgin) that wqb_llm_python uses in per-field strategy hints.
  • analyze_datafields() prints a full profiling report: column completeness,
    type distribution, category/subcategory breakdown, coverage stats, competition
    distribution, and dataset sources.
  • select_high_value_fields() provides a strategic filter/sort that prioritises
    virgin fields (alphaCount=0) and high-coverage fields — the two strongest
    predictors of successful alpha generation.
"""

import time
import pandas as pd
import ace_lib as ace


# ─────────────────────────────────────────────────────────────────────────────
# INTERNAL UTILITIES
# ─────────────────────────────────────────────────────────────────────────────

def _safe_numeric(series: pd.Series, default=None) -> pd.Series:
    """Coerce a Series to numeric, filling unconvertible values with default."""
    return pd.to_numeric(series, errors="coerce").fillna(default) if default is not None \
        else pd.to_numeric(series, errors="coerce")


def _enrich_datafields(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add computed metadata columns to the datafields DataFrame.
    These columns are:
      competition_tier — VIRGIN / LOW / MEDIUM / HIGH based on alphaCount
      coverage_tier    — HIGH / MEDIUM / LOW based on dateCoverage
      is_virgin        — True if alphaCount == 0

    They appear in the field summary passed to the LLM and drive research hints
    in wqb_llm_python._infer_strategy_hints().
    """
    df = df.copy()

    if "alphaCount" in df.columns:
        ac = _safe_numeric(df["alphaCount"], default=-1)

        def _comp_tier(n: float) -> str:
            n = int(n)
            if n <= 0:    return "VIRGIN"
            elif n < 10:  return "LOW"
            elif n < 50:  return "MEDIUM"
            else:         return "HIGH"

        df["competition_tier"] = ac.apply(_comp_tier)
        df["is_virgin"]        = ac.apply(lambda n: n == 0)

    if "dateCoverage" in df.columns:
        cov = _safe_numeric(df["dateCoverage"], default=0.0)

        def _cov_tier(v: float) -> str:
            if v >= 0.90:  return "HIGH"
            elif v >= 0.60: return "MEDIUM"
            else:           return "LOW"

        df["coverage_tier"] = cov.apply(_cov_tier)

    return df


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC — FETCH
# ─────────────────────────────────────────────────────────────────────────────

def fetch_datafields(
    selected_ids,
    *,
    s,
    instrument_type: str = "EQUITY",
    region: str = "",
    delay: int = 1,
    universe: str = "",
    data_type: str = "ALL",
    search: str = "",
    sleep_time: float = 2.0,
    max_retries: int = 3,
) -> pd.DataFrame:
    """
    Fetch datafields for one or more dataset IDs.

    Returns a single concatenated DataFrame with ALL columns preserved exactly
    as returned by ace.get_datafields(), plus:
      - dataset_id   : the requested dataset ID (added per batch)
      - competition_tier, coverage_tier, is_virgin  (added by _enrich_datafields)

    Args:
        selected_ids:    Iterable of dataset ID strings.
        s:               Authenticated ace_lib.SingleSession.
        instrument_type: Default "EQUITY".
        region:          Region code, e.g. "USA", "EUR".
        delay:           Delay setting (0 or 1).
        universe:        Universe filter, e.g. "TOP3000".
        data_type:       "ALL", "MATRIX", "SCALAR", etc.
        search:          Free-text search filter.
        sleep_time:      Seconds to sleep between dataset fetches.
        max_retries:     Retry attempts on KeyError (rate-limit proxy).

    Returns:
        pd.DataFrame with all available columns and enrichment metadata.
    """
    all_dfs: list[pd.DataFrame] = []

    for dataset_id in selected_ids:
        print(f"\nFetching: {dataset_id}")

        for attempt in range(max_retries):
            try:
                df = ace.get_datafields(
                    s=s,
                    instrument_type=instrument_type,
                    region=region,
                    delay=delay,
                    universe=universe,
                    dataset_id=dataset_id,
                    data_type=data_type,
                    search=search,
                )

                if df is not None and not df.empty:
                    df = df.copy()
                    # Preserve the dataset ID as a column even if ace already
                    # expanded it — useful as a fetch-context label
                    if "dataset_id" not in df.columns:
                        df["dataset_id"] = dataset_id
                    all_dfs.append(df)
                    print(f"  Success: {dataset_id} ({len(df)} fields, "
                          f"{df.shape[1]} columns)")
                    break
                else:
                    print(f"  Empty response for {dataset_id}")
                    break

            except KeyError:
                print(f"  Attempt {attempt + 1}/{max_retries} failed for "
                      f"{dataset_id}. Retrying …")
                if attempt < max_retries - 1:
                    time.sleep(sleep_time * (attempt + 1))
                else:
                    print(f"  Giving up after {max_retries} attempts: {dataset_id}")

        time.sleep(sleep_time)

    if not all_dfs:
        print("No datafields retrieved.")
        return pd.DataFrame()

    df_all = pd.concat(all_dfs, ignore_index=True)
    df_all = _enrich_datafields(df_all)

    print(f"\nFetch complete: {len(df_all)} total fields, "
          f"{df_all.shape[1]} columns (including enrichment metadata)")
    return df_all


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC — ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def analyze_datafields(df: pd.DataFrame) -> None:
    """
    Print a comprehensive profiling report of the fetched datafields DataFrame.

    Sections:
      1. Column completeness inventory
      2. Data type (MATRIX / SCALAR / INT …) distribution
      3. Category and subcategory breakdown
      4. Date coverage distribution and tier summary
      5. Alpha competition (alphaCount) stats and virgin field count
      6. Dataset source breakdown
      7. Top fields by pyramid multiplier

    Args:
        df: DataFrame from fetch_datafields().
    """
    if df.empty:
        print("DataFrame is empty — nothing to analyse.")
        return

    sep = "=" * 64
    print(f"\n{sep}")
    print(f"  DATAFIELDS PROFILE")
    print(f"  {len(df)} fields   {df.shape[1]} columns")
    print(f"{sep}")

    # ── 1. Column completeness ─────────────────────────────────────────────
    print(f"\n{'─'*48}")
    print(f"  COLUMN INVENTORY ({df.shape[1]} columns)")
    print(f"{'─'*48}")
    max_col_len = max(len(c) for c in df.columns)
    for col in df.columns:
        non_null = int(df[col].notna().sum())
        pct = non_null / len(df) * 100
        bar = "█" * int(pct / 5)
        print(f"  {col:<{max_col_len}}  {non_null:>5}/{len(df)}  ({pct:5.1f}%)  {bar}")

    # ── 2. Data type distribution ──────────────────────────────────────────
    if "type" in df.columns:
        print(f"\n{'─'*48}")
        print("  DATA TYPE DISTRIBUTION")
        print(f"{'─'*48}")
        for dtype, cnt in df["type"].value_counts().items():
            print(f"  {str(dtype):<14}  {cnt:>5}  ({cnt/len(df)*100:.1f}%)")

    # ── 3. Category breakdown ──────────────────────────────────────────────
    cat_col    = "category_name" if "category_name" in df.columns else "category"
    subcat_col = "subcategory_name" if "subcategory_name" in df.columns else "subcategory"

    if cat_col in df.columns:
        print(f"\n{'─'*48}")
        print(f"  CATEGORY ({cat_col})")
        print(f"{'─'*48}")
        for cat, cnt in df[cat_col].value_counts().head(15).items():
            print(f"  {str(cat):<40}  {cnt:>5}")

    if subcat_col in df.columns and subcat_col != cat_col:
        print(f"\n{'─'*48}")
        print(f"  SUBCATEGORY ({subcat_col}) — top 15")
        print(f"{'─'*48}")
        for sc, cnt in df[subcat_col].value_counts().head(15).items():
            print(f"  {str(sc):<40}  {cnt:>5}")

    # ── 4. Coverage distribution ───────────────────────────────────────────
    if "dateCoverage" in df.columns:
        cov = _safe_numeric(df["dateCoverage"]).dropna()
        print(f"\n{'─'*48}")
        print("  DATE COVERAGE")
        print(f"{'─'*48}")
        if not cov.empty:
            print(f"  min={cov.min():.4f}  p25={cov.quantile(.25):.4f}  "
                  f"median={cov.median():.4f}  p75={cov.quantile(.75):.4f}  "
                  f"max={cov.max():.4f}")
        if "coverage_tier" in df.columns:
            for tier, cnt in df["coverage_tier"].value_counts().items():
                print(f"  {tier:<10}  {cnt:>5}  ({cnt/len(df)*100:.1f}%)")

    # ── 5. Competition stats ───────────────────────────────────────────────
    if "alphaCount" in df.columns:
        ac = _safe_numeric(df["alphaCount"]).dropna()
        print(f"\n{'─'*48}")
        print("  ALPHA COMPETITION (alphaCount)")
        print(f"{'─'*48}")
        if not ac.empty:
            virgin = int((ac == 0).sum())
            print(f"  Virgin fields (alphaCount=0): {virgin} / {len(ac)} "
                  f"({virgin/len(ac)*100:.1f}%)")
            print(f"  alphaCount — min:{int(ac.min())}  "
                  f"median:{ac.median():.0f}  "
                  f"max:{int(ac.max())}")
        if "competition_tier" in df.columns:
            for tier, cnt in df["competition_tier"].value_counts().items():
                print(f"  {tier:<10}  {cnt:>5}  ({cnt/len(df)*100:.1f}%)")

    # ── 6. Dataset sources ─────────────────────────────────────────────────
    ds_col = "dataset_name" if "dataset_name" in df.columns else "dataset_id"
    if ds_col in df.columns:
        print(f"\n{'─'*48}")
        print(f"  DATASET SOURCES ({ds_col})")
        print(f"{'─'*48}")
        for ds, cnt in df[ds_col].value_counts().items():
            print(f"  {str(ds):<50}  {cnt:>5}")

    # ── 7. Top fields by pyramid multiplier ───────────────────────────────
    if "pyramidMultiplier" in df.columns:
        pm = _safe_numeric(df["pyramidMultiplier"]).dropna()
        if not pm.empty and pm.max() > 1.0:
            top_pm = (
                df[["id", "pyramidMultiplier", "alphaCount"]]
                .copy()
                .assign(pyramidMultiplier=_safe_numeric(df["pyramidMultiplier"]))
                .sort_values("pyramidMultiplier", ascending=False)
                .head(10)
            )
            print(f"\n{'─'*48}")
            print("  TOP 10 FIELDS BY PYRAMID MULTIPLIER")
            print(f"{'─'*48}")
            for _, row in top_pm.iterrows():
                print(f"  {str(row['id']):<45}  PM={row['pyramidMultiplier']:.2f}  "
                      f"alphaCount={int(row.get('alphaCount', 0))}")

    print(f"\n{sep}\n")


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC — FIELD SELECTION STRATEGY
# ─────────────────────────────────────────────────────────────────────────────

def select_high_value_fields(
    df: pd.DataFrame,
    max_fields: int = 50,
    prefer_virgin: bool = True,
    prefer_high_coverage: bool = True,
    prefer_high_pyramid: bool = False,
    min_coverage: float = 0.0,
    exclude_high_competition: bool = False,
    high_competition_threshold: int = 100,
) -> pd.DataFrame:
    """
    Strategically select and rank fields for alpha generation.

    Priority logic (all flags default to reasonable values):
      1. Filter by min_coverage (removes chronically sparse fields).
      2. Optionally exclude crowded fields (alphaCount > threshold).
      3. Sort: virgin fields first (if prefer_virgin), then by coverage
         descending (if prefer_high_coverage), then by pyramidMultiplier
         descending (if prefer_high_pyramid).
      4. Return the top max_fields rows.

    Args:
        df:                         Full datafields DataFrame.
        max_fields:                 Maximum number of fields to return.
        prefer_virgin:              Sort alphaCount=0 fields first.
        prefer_high_coverage:       Sort by dateCoverage descending.
        prefer_high_pyramid:        Sort by pyramidMultiplier descending.
        min_coverage:               Drop fields with dateCoverage < this value.
        exclude_high_competition:   Drop fields with alphaCount > threshold.
        high_competition_threshold: alphaCount threshold for exclusion.

    Returns:
        Filtered and ranked DataFrame with at most max_fields rows.
    """
    result = df.copy()
    n_original = len(result)

    # ── Step 1: coverage filter ────────────────────────────────────────────
    if min_coverage > 0 and "dateCoverage" in result.columns:
        cov = _safe_numeric(result["dateCoverage"], default=0.0)
        result = result[cov >= min_coverage].copy()
        print(f"Coverage filter (>= {min_coverage:.2f}): "
              f"{len(result)}/{n_original} fields retained")

    # ── Step 2: competition filter ─────────────────────────────────────────
    if exclude_high_competition and "alphaCount" in result.columns:
        ac = _safe_numeric(result["alphaCount"], default=0)
        result = result[ac <= high_competition_threshold].copy()
        print(f"Competition filter (<= {high_competition_threshold}): "
              f"{len(result)} fields retained")

    # ── Step 3: sorting ────────────────────────────────────────────────────
    sort_keys:  list[str] = []
    sort_asc:   list[bool] = []
    temp_cols:  list[str] = []

    if prefer_virgin and "alphaCount" in result.columns:
        col = "_sort_alpha_count"
        result[col] = _safe_numeric(result["alphaCount"], default=999.0)
        sort_keys.append(col); sort_asc.append(True)    # 0 sorts first
        temp_cols.append(col)

    if prefer_high_coverage and "dateCoverage" in result.columns:
        col = "_sort_date_coverage"
        result[col] = _safe_numeric(result["dateCoverage"], default=0.0)
        sort_keys.append(col); sort_asc.append(False)   # highest first
        temp_cols.append(col)

    if prefer_high_pyramid and "pyramidMultiplier" in result.columns:
        col = "_sort_pyramid"
        result[col] = _safe_numeric(result["pyramidMultiplier"], default=0.0)
        sort_keys.append(col); sort_asc.append(False)   # highest first
        temp_cols.append(col)

    if sort_keys:
        result = result.sort_values(by=sort_keys, ascending=sort_asc)

    result = result.drop(columns=temp_cols, errors="ignore")

    # ── Step 4: cap ────────────────────────────────────────────────────────
    result = result.head(max_fields).reset_index(drop=True)

    # ── Summary ────────────────────────────────────────────────────────────
    virgin_n = int(result.get("is_virgin", pd.Series(False)).sum()) \
        if "is_virgin" in result.columns else "n/a"
    high_cov_n = int((result["coverage_tier"] == "HIGH").sum()) \
        if "coverage_tier" in result.columns else "n/a"

    print(
        f"Selected {len(result)} fields  "
        f"(virgin: {virgin_n}, high-coverage: {high_cov_n}, "
        f"prefer_virgin={prefer_virgin}, prefer_high_coverage={prefer_high_coverage})"
    )
    return result