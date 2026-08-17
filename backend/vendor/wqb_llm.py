import json as _json
import re
import time
import random

VEC_OPS = ["vec_avg", "vec_sum", "vec_max", "vec_min", "vec_norm", "vec_stddev", "vec_range"]

def fetch_alpha_expression(session, alpha_id):
    """Fetch alpha expression from WorldQuant Brain."""
    try:
        response = session.get(f"https://api.worldquantbrain.com/alphas/{alpha_id}")
        alpha_details = response.json()

        alpha_expression = (
            alpha_details.get('regular', {}).get('code') or
            alpha_details.get('code') or
            alpha_details.get('expression')
        )

        return alpha_expression if alpha_expression else None

    except Exception as e:
        print(f"Alpha fetch error: {e}")
        return None

def _build_operator_summary(regular_ops_df, max_ops=130):
    rows = []
    name_col = next((c for c in ["name", "id"] if c in regular_ops_df.columns), None)
    desc_col = next((c for c in ["definition", "description", "desc"] if c in regular_ops_df.columns), None)
    for _, r in regular_ops_df.head(max_ops).iterrows():
        name = r[name_col] if name_col else str(r.iloc[0])
        desc = str(r[desc_col])[:130] if desc_col else ""
        rows.append(f"  {name}: {desc}" if desc else f"  {name}")
    return "\n".join(rows)

def _build_field_summary(df, max_fields=40):
    rows = []
    desc_col = next((c for c in ["description", "desc", "name"] if c in df.columns), None)
    for _, r in df.head(max_fields).iterrows():
        fid = r["id"]
        ftype = str(r.get("type", "MATRIX")).upper()
        desc = str(r[desc_col])[:60] if desc_col else ""
        tag = "[VECTOR]" if ftype == "VECTOR" else "[MATRIX]"
        rows.append(f"  {tag} {fid}: {desc}" if desc else f"  {tag} {fid}")
    return "\n".join(rows)

def _extract_expressions(text):
    MARKERS = ["rank(", "ts_", "vec_", "purify(", "group_"]
    text = re.sub(r"```[\w]*", "", text).strip()

    try:
        arr = _json.loads(text)
        if isinstance(arr, list):
            return [str(e).strip() for e in arr if e]
    except _json.JSONDecodeError:
        pass

    m = re.search(r'\[.*?\]', text, re.DOTALL)
    if m:
        try:
            arr = _json.loads(m.group())
            if isinstance(arr, list):
                return [str(e).strip() for e in arr if e]
        except _json.JSONDecodeError:
            pass

    candidates = re.findall(r'"([^"\n]{10,})"', text)
    exprs = [c.strip() for c in candidates if any(kw in c for kw in MARKERS)]
    if exprs:
        return exprs

    lines = [l.strip().strip(',').strip('"') for l in text.splitlines() if l.strip()]
    return [l for l in lines if any(kw in l for kw in MARKERS)]

def _is_retryable_gemini_error(exc):
    msg = str(exc).upper()
    retry_markers = [
        "503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
        "500", "INTERNAL", "504", "DEADLINE_EXCEEDED"
    ]
    return any(marker in msg for marker in retry_markers)

def _gemini_generate_with_retry(client, prompt, primary_model, fallback_models=None, max_retries_per_model=5, base_delay=2.0, max_delay=30.0):
    fallback_models = fallback_models or []
    models_to_try = [primary_model] + list(fallback_models)
    last_exc = None

    for model_name in models_to_try:
        for attempt in range(max_retries_per_model):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return response, model_name

            except Exception as exc:
                last_exc = exc
                if not _is_retryable_gemini_error(exc):
                    raise
                
                sleep_s = min(max_delay, base_delay * (2 ** attempt))
                sleep_s += random.uniform(0, 0.8)
                print(f"  Retryable error on {model_name} (attempt {attempt + 1}): {exc}")
                print(f"  Sleeping {sleep_s:.1f}s before retry...")
                time.sleep(sleep_s)

        print(f"  Switching model after failures: {model_name}")
    raise last_exc

def generate_alpha_hypothesis(client, alpha_expression, primary_model, fallback_models=None):
    prompt = f"""You are a senior quantitative researcher.
Given the following WorldQuant alpha expression:
{alpha_expression}
Your task is to reverse-engineer the underlying IDEA and express it as a clear hypothesis.
=== OUTPUT REQUIREMENTS ===
Return ONLY a concise hypothesis (3–6 lines), covering:
1. Signal intuition (what it captures)
2. Type (momentum / mean reversion / volatility / etc.)
3. Why it might work (economic or statistical reasoning)
4. What variations could improve it
No code. No expressions. Only reasoning."""

    try:
        response, used_model = _gemini_generate_with_retry(client, prompt, primary_model, fallback_models)
        hypothesis = (getattr(response, "text", "") or "").strip()
        print(f"Hypothesis generated using {used_model}")
        return hypothesis
    except Exception as e:
        print(f"Hypothesis generation failed: {e}")
        return None

def generate_expressions_with_gemini(
    client, datafields_df, regular_ops_df, n_per_field=1, batch_size=4, max_fields=10, 
    sleep_between_batches=5.0, primary_model="gemini-2.5-flash", fallback_models=None, 
    max_retries_per_model=5, base_delay=2.0, max_delay=30.0, # <-- Added these
    alpha_id=None, session=None
):
    op_summary = _build_operator_summary(regular_ops_df)
    vec_ops_str = ", ".join(VEC_OPS)
    df_slice = datafields_df.head(max_fields).reset_index(drop=True)
    all_exprs = []

    hypothesis = None
    base_alpha_expression = None

    if alpha_id and session:
        print(f"\n🔎 Fetching base alpha: {alpha_id}")
        base_alpha_expression = fetch_alpha_expression(session, alpha_id)
        if base_alpha_expression:
            hypothesis = generate_alpha_hypothesis(client, base_alpha_expression, primary_model, fallback_models)
        else:
            print("⚠️ Failed to fetch base alpha expression.")

    n_batches = (len(df_slice) + batch_size - 1) // batch_size

    for i, batch_start in enumerate(range(0, len(df_slice), batch_size)):
        batch = df_slice.iloc[batch_start: batch_start + batch_size]
        field_summary = _build_field_summary(batch)
        total_needed = len(batch) * n_per_field

        prompt = f"You are an expert WorldQuant Brain alpha researcher.\n\n=== RESEARCH CONTEXT ===\n"

        if hypothesis:
            prompt += (
                f"You MUST derive new alpha ideas from the hypothesis below.\n\n{hypothesis}\n\n"
                f"Guidelines:\n- Preserve the CORE IDEA\n- Explore variations\n- Introduce structural diversity\n"
                f"- DO NOT replicate the original alpha\n\n- IMPORTANT TYPE RULE:\n"
                f"  Operator selection MUST depend ONLY on the CURRENT field type.\n"
                f"  Do NOT imitate vec_* usage from reference alpha unless current field is [VECTOR].\n\n"
            )

        if base_alpha_expression:
            prompt += f"Reference Alpha (DO NOT copy):\n{base_alpha_expression}\n\n"

        prompt += (
            f"Generate exactly {total_needed} diverse, economically intuitive, original alpha expressions using WorldQuant Fast Expression language.\n\n"
        
            f"========================\n"
            f"OBJECTIVE\n"
            f"========================\n"
            f"Generate high-quality alpha candidates that are economically meaningful, structurally diverse, and mathematically valid.\n"
            f"Each alpha must be derived from the economic interpretation of a SINGLE datafield.\n"
            f"Operator selection must be driven by the field description, not randomly chosen.\n\n"
        
            f"========================\n"
            f"STRICT RULES\n"
            f"========================\n"
            f"1. Use ONLY operators from the AVAILABLE REGULAR OPERATORS list.\n"
            f"2. Strictly obey every operator signature and argument requirement.\n"
            f"3. Window/lookback parameters must be positive integers.\n"
            f"4. EXACTLY ONE raw datafield per expression.\n"
            f"5. Multiple datafields are strictly forbidden.\n"
            f"6. An expression is invalid if more than one datafield appears anywhere directly or indirectly.\n"
            f"7. Use between 1 and 3 operators maximum.\n"
            f"8. Expressions must be structurally distinct.\n"
            f"9. Avoid trivial operator wrapping.\n"
            f"10. Prefer economically motivated transformations.\n"
            f"11. VECTOR fields REQUIRE vec_* reduction before other operations.\n"
            f"12. MATRIX fields MUST NEVER use vec_* operators.\n"
            f"13. GROUP operators may only use:\n"
            f"       industry\n"
            f"       subindustry\n"
            f"       market\n"
            f"       exchange\n"
            f"       currency\n"
            f"14. Any other grouping identifier is invalid.\n"
            f"15. Output ONLY valid JSON.\n\n"
        
            f"========================\n"
            f"ECONOMIC REASONING REQUIREMENT (MANDATORY)\n"
            f"========================\n"
            f"For EVERY datafield:\n\n"
        
            f"Step 1:\n"
            f"Read BOTH:\n"
            f"    - field name\n"
            f"    - field description\n\n"
        
            f"Step 2:\n"
            f"Determine what economic phenomenon the field measures.\n\n"
        
            f"Possible categories:\n"
            f"    valuation\n"
            f"    growth\n"
            f"    profitability\n"
            f"    quality\n"
            f"    analyst expectations\n"
            f"    revisions\n"
            f"    sentiment\n"
            f"    volatility\n"
            f"    risk\n"
            f"    liquidity\n"
            f"    leverage\n"
            f"    ownership flow\n"
            f"    institutional flow\n"
            f"    acceleration\n"
            f"    stability\n"
            f"    dispersion\n"
            f"    seasonality\n"
            f"    crowding\n"
            f"    positioning\n\n"
        
            f"Step 3:\n"
            f"Form an economic hypothesis.\n\n"
        
            f"Examples:\n"
            f"    valuation -> relative cheapness/expensiveness\n"
            f"    analyst revisions -> expectation momentum\n"
            f"    volatility -> instability and risk regime shifts\n"
            f"    liquidity -> trading pressure and participation\n"
            f"    profitability -> persistent quality premium\n"
            f"    leverage -> balance-sheet risk\n"
            f"    ownership flow -> accumulation/distribution\n\n"
        
            f"Step 4:\n"
            f"Choose operators that naturally extract that behavior.\n\n"
        
            f"Step 5:\n"
            f"Generate expression.\n\n"
        
            f"Do NOT randomly attach operators.\n"
            f"Operator selection must be justified by the economic meaning of the field.\n\n"
        
            f"========================\n"
            f"OPERATOR-ECONOMIC FITTING GUIDE\n"
            f"========================\n"
        
            f"Momentum-like fields:\n"
            f"    prefer ts_delta\n"
            f"    prefer ts_rank\n"
            f"    prefer ts_zscore\n"
            f"    prefer ts_decay_exp_window\n"
            f"    prefer ts_scale\n\n"
        
            f"Volatility-like fields:\n"
            f"    prefer ts_std_dev\n"
            f"    prefer ts_skewness\n"
            f"    prefer ts_kurtosis\n"
            f"    prefer ts_co_skewness\n"
            f"    prefer ts_zscore\n\n"
        
            f"Flow-like fields:\n"
            f"    prefer ts_decay_exp_window\n"
            f"    prefer ts_rank\n"
            f"    prefer ts_delta\n"
            f"    prefer ts_arg_max\n"
            f"    prefer ts_arg_min\n\n"
        
            f"Stability-like fields:\n"
            f"    prefer ts_av_diff\n"
            f"    prefer ts_std_dev\n"
            f"    prefer ts_zscore\n\n"
        
            f"Event-like fields:\n"
            f"    prefer ts_arg_max\n"
            f"    prefer ts_arg_min\n"
            f"    prefer ts_delta\n\n"
        
            f"Cross-sectional valuation fields:\n"
            f"    prefer rank\n"
            f"    prefer group_rank\n"
            f"    prefer group_zscore\n"
            f"    prefer group_neutralize\n\n"
        
            f"Relative peer comparisons:\n"
            f"    prefer group_rank\n"
            f"    prefer group_zscore\n"
            f"    prefer group_neutralize\n\n"
        
            f"========================\n"
            f"UNCOMMON OPERATOR EXPLORATION (CRITICAL)\n"
            f"========================\n"
        
            f"Do not overuse safe operators.\n\n"
        
            f"Avoid repeatedly generating:\n"
            f"    rank\n"
            f"    ts_rank\n"
            f"    ts_mean\n"
            f"    ts_zscore\n\n"
        
            f"When economically appropriate, actively explore:\n"
            f"    ts_co_skewness\n"
            f"    ts_kurtosis\n"
            f"    ts_arg_max\n"
            f"    ts_arg_min\n"
            f"    ts_av_diff\n"
            f"    ts_count_nans\n"
            f"    ts_decay_exp_window\n"
            f"    group_rank\n"
            f"    group_zscore\n"
            f"    group_neutralize\n"
            f"    group_backfill\n"
            f"    densify\n"
            f"    kth_element\n"
            f"    reverse\n"
            f"    signed_power\n"
            f"    if_else\n"
            f"    trade_when\n\n"
        
            f"Favor genuinely different economic constructions rather than operator permutations.\n\n"
        
            f"========================\n"
            f"DIVERSITY TARGETS\n"
            f"========================\n"
        
            f"Across the generated batch:\n\n"
        
            f"- At least 25% should contain group operators.\n"
            f"- At least 25% should contain uncommon time-series operators.\n"
            f"- No single operator should dominate the batch.\n"
            f"- Avoid repeating the same economic idea.\n"
            f"- Avoid simple lookback substitutions.\n"
            f"- Prefer different operator families.\n\n"
        
            f"Examples of weak diversity:\n"
            f"    ts_rank(x,20)\n"
            f"    ts_rank(x,60)\n"
            f"    ts_rank(x,120)\n\n"
        
            f"Examples of strong diversity:\n"
            f"    ts_co_skewness(x,60)\n"
            f"    group_zscore(x,industry)\n"
            f"    ts_arg_max(x,120)\n"
            f"    signed_power(rank(x),2)\n\n"
        
            f"========================\n"
            f"HIDDEN REASONING PROCESS (DO NOT OUTPUT)\n"
            f"========================\n"
        
            f"For each candidate internally determine:\n\n"
        
            f"FIELD THESIS:\n"
            f"    What does the field measure?\n\n"
        
            f"SIGNAL HYPOTHESIS:\n"
            f"    What alpha behavior could exist?\n\n"
        
            f"OPERATOR RATIONALE:\n"
            f"    Which operator best extracts that behavior?\n\n"
        
            f"Only then generate the expression.\n"
            f"Do NOT output the reasoning.\n\n"
        
            f"========================\n"
            f"VALID GROUPS\n"
            f"========================\n"
        
            f"industry\n"
            f"subindustry\n"
            f"market\n"
            f"exchange\n"
            f"currency\n\n"
        
            f"========================\n"
            f"AVAILABLE REGULAR OPERATORS\n"
            f"========================\n"
            f"{op_summary}\n\n"
        
            f"========================\n"
            f"DATAFIELDS\n"
            f"========================\n"
            f"{field_summary}\n\n"
        
            f"========================\n"
            f"OUTPUT FORMAT\n"
            f"========================\n"
        
            f"Output ONLY a JSON array of strings.\n"
            f"No markdown.\n"
            f"No explanations.\n"
            f"No comments.\n"
            f"No reasoning.\n\n"
        
            f'Example:\n'
            f'["ts_arg_max(field1,120)", "group_zscore(field2,industry)"]'
        )

        print(f"[{i+1}/{n_batches}] Generating for fields {batch_start+1}–{batch_start+len(batch)} ...")

        try:
            response, used_model = _gemini_generate_with_retry(
                client, prompt, primary_model, fallback_models, max_retries_per_model=5, base_delay=2.0, max_delay=30.0
            )
            text = getattr(response, "text", "") or ""
            exprs = _extract_expressions(text)
            if exprs:
                print(f"  Parsed {len(exprs)} expressions")
                all_exprs.extend(exprs)
        except Exception as exc:
            print(f"  Gemini API error: {exc}")

        time.sleep(sleep_between_batches)

    unique = list(set(all_exprs))
    print(f"\nTotal unique AI-generated expressions: {len(unique)}\nFirst 10:")
    for i, expr in enumerate(unique[:10], 1):
        print(f"{i}. {expr}")

    return unique