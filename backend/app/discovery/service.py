"""ACE Research Intelligence layer.

Turns Markdown research reports into structured hypotheses, maps those hypotheses to
selected BRAIN fields, creates experiments, and records Alpha DNA / failures. It is
intentionally conservative: the LLM proposes research structure; ACE's validator and
field catalogue remain the source of truth for executable expressions.
"""
from __future__ import annotations

import hashlib, json, re, time
from collections import Counter
from itertools import combinations

from sqlalchemy import select, func
from app.db.base import SessionLocal
from app.db import models as M
from app.knowledge import service as knowledge_service


def canonical(expr: str) -> str:
    return re.sub(r"\s+", "", (expr or "").strip().lower())


def _safe_json(text: str, fallback=None):
    try:
        obj=json.loads(text)
        return obj
    except Exception:
        return fallback if fallback is not None else {}


def _extract_title(md: str, fallback: str = "") -> str:
    for line in (md or "").splitlines():
        m = re.match(r"^#\s+(.+)$", line.strip())
        if m:
            return m.group(1).strip()[:240]
    return fallback or "Research report"


def _metadata(md: str) -> dict:
    text = md or ""
    def pick(pattern: str) -> str:
        m = re.search(pattern, text, re.I | re.M)
        return m.group(1).strip()[:240] if m else ""
    return {
        "title": _extract_title(text),
        "authors": pick(r"^(?:authors?|by)\s*:\s*(.+)$"),
        "year": pick(r"^(?:year|published|publication\s*year)\s*:\s*(\d{4})$"),
        "source": pick(r"^(?:source|journal|venue)\s*:\s*(.+)$"),
        "doi": pick(r"^(?:doi)\s*:\s*(.+)$"),
    }


def _parse_markdown(md: str) -> dict:
    """Parse Markdown into a provenance-preserving intermediate document.

    This is deliberately deterministic. It does not decide whether a claim is true; it
    records where text came from so the research LLM can distinguish source material from
    inference later.
    """
    lines = (md or "").splitlines()
    sections, tables, bullets, numbered, code_blocks, equations = [], [], [], [], [], []
    current = {"title": "Document", "level": 0, "start_line": 1, "end_line": len(lines), "text": []}
    in_code = False; code_start = 0; code_buf = []
    i = 0
    while i < len(lines):
        raw = lines[i]; stripped = raw.strip(); line_no = i + 1
        if stripped.startswith("```") or stripped.startswith("~~~"):
            if not in_code:
                in_code = True; code_start = line_no; code_buf = [raw]
            else:
                code_buf.append(raw); code_blocks.append({"start_line": code_start, "end_line": line_no, "text": "\n".join(code_buf)})
                in_code = False; code_buf = []
            i += 1; continue
        if in_code:
            code_buf.append(raw); i += 1; continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", stripped)
        if m:
            if current["text"] or current["title"] != "Document":
                current["end_line"] = line_no - 1
                current["text"] = "\n".join(current["text"]).strip()
                sections.append(current)
            current = {"title": m.group(2).strip(), "level": len(m.group(1)), "start_line": line_no, "end_line": len(lines), "text": []}
            i += 1; continue
        if re.match(r"^[-*+]\s+", stripped):
            bullets.append({"line": line_no, "text": re.sub(r"^[-*+]\s+", "", stripped)})
        elif re.match(r"^\d+[.)]\s+", stripped):
            numbered.append({"line": line_no, "text": re.sub(r"^\d+[.)]\s+", "", stripped)})
        if stripped.startswith("|") and "|" in stripped[1:]:
            block = [stripped]; j = i + 1
            while j < len(lines) and lines[j].strip().startswith("|"):
                block.append(lines[j].strip()); j += 1
            if len(block) >= 2:
                tables.append({"start_line": line_no, "end_line": j, "rows": [x.strip("|").split("|") for x in block]})
            i = j; continue
        if re.search(r"(?:equation|formula|hypothesis|h\d+|\b[\w]+\s*=\s*[^=])", stripped, re.I) and ("=" in stripped or re.match(r"^(?:equation|formula)\b", stripped, re.I)):
            equations.append({"line": line_no, "text": stripped})
        if stripped:
            current["text"].append(stripped)
        i += 1
    if in_code and code_buf:
        code_blocks.append({"start_line": code_start, "end_line": len(lines), "text": "\n".join(code_buf)})
    current["end_line"] = len(lines); current["text"] = "\n".join(current["text"]).strip()
    if current["text"] or not sections: sections.append(current)
    return {"line_count": len(lines), "metadata": _metadata(md), "sections": sections, "bullets": bullets[:250],
            "numbered": numbered[:250], "tables": tables[:50], "code_blocks": code_blocks[:20], "equations": equations[:100]}


def ingest_document(content: str, title: str = "", source: str = "markdown") -> dict:
    content=(content or "").strip()
    h=hashlib.sha256(content.encode("utf-8")).hexdigest()
    with SessionLocal() as db:
        old=db.scalar(select(M.ResearchDocument).where(M.ResearchDocument.content_hash==h))
        if old:
            return {"id":old.id,"duplicate":True,"title":old.title}
        parsed = _parse_markdown(content)
        row=M.ResearchDocument(title=(title.strip() or parsed["metadata"].get("title") or _extract_title(content)), source=source,
                               content=content, content_hash=h)
        db.add(row); db.commit()
        return {"id":row.id,"duplicate":False,"title":row.title,"chars":len(content),"parser":{"lines":parsed["line_count"],"sections":len(parsed["sections"]),"tables":len(parsed["tables"]),"bullets":len(parsed["bullets"])} }


def _heuristic_extract(md: str) -> dict:
    parsed = _parse_markdown(md)
    bullets = [b["text"] for b in parsed["bullets"]] + [b["text"] for b in parsed["numbered"]]
    sections = {s["title"].strip().lower(): {"start_line":s["start_line"], "end_line":s["end_line"], "text":s["text"]} for s in parsed["sections"]}
    var_pat=re.compile(r"\b(?:[A-Za-z][A-Za-z0-9_]{2,})(?:\s+(?:ratio|yield|growth|change|return|surprise|momentum|margin|quality|value|volatility|accrual|liquidity))?\b")
    variables=[]
    for x in bullets:
        for m in var_pat.findall(x):
            if len(m)>3 and m.lower() not in {"the","this","that","with","from","market","returns"}: variables.append(m)
    finding_rows=[]
    for b in parsed["bullets"][:30]: finding_rows.append({"text":b["text"],"evidence":"SOURCE_SUPPORTED","source_lines":[b["line"]]})
    return {"title": parsed["metadata"].get("title") or _extract_title(md), "metadata": parsed["metadata"],
            "research_question": "", "findings": finding_rows, "mechanisms": [],
            "variables": list(dict.fromkeys(variables))[:100], "hypotheses": [], "conditions": [], "horizons": [],
            "limitations": [], "source_sections": sections, "parser": parsed}


def _coerce_research_json(value) -> dict:
    """Parse the common structured-response variants returned by OpenRouter/OpenAI-compatible models.

    The previous implementation called json.loads() directly on the first extracted string.
    That fails on single-quoted Python-dict output, fenced JSON, prose-wrapped JSON, and a
    one-item array wrapper. These are transport/format issues, not research failures.
    """
    import ast
    if isinstance(value, dict):
        return value
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    candidates = [text]
    # Recover an object or one-item array embedded in short explanatory prose.
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, text, re.S)
        if m and m.group(0) not in candidates:
            candidates.append(m.group(0))
    last = None
    for candidate in candidates:
        for parser in (json.loads, ast.literal_eval):
            try:
                obj = parser(candidate)
                if isinstance(obj, list) and len(obj) == 1 and isinstance(obj[0], dict):
                    obj = obj[0]
                if isinstance(obj, dict):
                    return obj
            except Exception as exc:
                last = exc
    raise ValueError("LLM returned an invalid research JSON object") from last


def _normalise_llm_research(obj: dict, base: dict) -> dict:
    """Normalise and validate the model's research map without inventing content."""
    if not isinstance(obj, dict):
        return base
    out = dict(base)
    for k in ("title", "research_question", "conditions", "horizons", "limitations", "variables", "mechanisms"):
        if k in obj:
            out[k] = obj[k]

    # Findings are kept as objects so the UI can show provenance. Accept a few common
    # model spellings, but never fabricate source lines.
    raw_findings = obj.get("findings", [])
    findings = []
    if isinstance(raw_findings, list):
        for f in raw_findings:
            if isinstance(f, str):
                findings.append({"text": f.strip(), "evidence": "SOURCE_SUPPORTED", "source_lines": []})
            elif isinstance(f, dict):
                x = dict(f)
                x["text"] = str(x.get("text") or x.get("claim") or x.get("statement") or "").strip()
                if not x["text"]:
                    continue
                ev = str(x.get("evidence") or x.get("evidence_type") or "SOURCE_SUPPORTED").upper()
                x["evidence"] = "MODEL_INFERENCE" if ev == "MODEL_INFERENCE" else "SOURCE_SUPPORTED"
                x["source_lines"] = _clean_source_lines(x.get("source_lines"))
                findings.append(x)
    if findings:
        out["findings"] = findings[:60]

    hs = []
    raw_hypotheses = obj.get("hypotheses", [])
    if isinstance(raw_hypotheses, list):
        for h in raw_hypotheses:
            if isinstance(h, str):
                h = {"statement": h}
            if not isinstance(h, dict):
                continue
            x = dict(h)
            x["statement"] = str(x.get("statement") or x.get("idea") or x.get("claim") or "").strip()
            x["mechanism"] = str(x.get("mechanism") or "").strip()
            x["expected_sign"] = str(x.get("expected_sign") or x.get("sign") or "unknown").strip()
            x["horizon"] = str(x.get("horizon") or "").strip()
            try:
                x["confidence"] = max(1, min(5, int(float(x.get("confidence", 0)))))
            except (TypeError, ValueError):
                x["confidence"] = 0
            ev = str(x.get("evidence") or x.get("evidence_type") or "MODEL_INFERENCE").upper()
            x["evidence"] = "SOURCE_SUPPORTED" if ev == "SOURCE_SUPPORTED" else "MODEL_INFERENCE"
            x["source_lines"] = _clean_source_lines(x.get("source_lines"))
            if x["statement"]:
                hs.append(x)
    out["hypotheses"] = hs[:40]
    # Normalise the economic concepts without forcing them into BRAIN identifiers.
    vars_out = []
    raw_vars = out.get("variables", [])
    if isinstance(raw_vars, list):
        for v in raw_vars:
            if isinstance(v, str):
                name = v.strip()
                if name: vars_out.append(name)
            elif isinstance(v, dict):
                name = str(v.get("name") or v.get("variable") or v.get("concept") or "").strip()
                if name: vars_out.append({**v, "name": name})
    out["variables"] = vars_out[:100]
    out["provenance"] = {"source_grounded": True}
    return out


def _clean_source_lines(value) -> list[int]:
    """Return only positive integer source lines. No line is guessed."""
    if not isinstance(value, (list, tuple)):
        return []
    out = []
    for x in value:
        try:
            n = int(x)
        except (TypeError, ValueError):
            continue
        if n > 0 and n not in out:
            out.append(n)
    return out[:20]


def _research_llm_prompt(base: dict, *, recovery: bool = False, enrichment: bool = False) -> str:
    parser = base.get("parser", {})
    if recovery:
        return (
            "You are repairing a quantitative-finance research analysis. The previous structured analysis was too sparse "
            "or did not produce usable hypotheses. Read the complete line-addressed Markdown report and recover additional "
            "distinct, defensible research content that is explicitly supported by the source or is a clearly labelled "
            "MODEL_INFERENCE. Do not invent empirical results, citations, BRAIN field IDs, datasets, or operators. "
            "Return ONLY one JSON object with keys findings, mechanisms, variables, hypotheses, conditions, horizons, limitations. "
            "Aim for comprehensive coverage: 10-60 findings, 10-50 mechanisms, 15-100 economic variables, and 8-30 distinct "
            "testable hypotheses when the report supports them. Preserve source_lines wherever possible.\n\nLINE-ADDRESSED REPORT:\n" +
            json.dumps(parser, ensure_ascii=False)[:160000]
        )
    if enrichment:
        return (
            "You are the enrichment stage of ACE Studio's quantitative research parser. The first pass extracted a sparse research map. "
            "Re-read the line-addressed report and the existing extraction below. Add only NEW, materially distinct findings, mechanisms, "
            "economic variables, and testable hypotheses supported by the source. Do not repeat existing items and do not invent facts. "
            "Variables are economic concepts, not BRAIN field names. Prioritise MODEL_INFERENCE hypotheses this pass: for each existing "
            "mechanism/finding, ask what else would have to be true if it's real that the authors never framed as a hypothesis — a "
            "different horizon, a control variable mentioned in passing, an unstated implication, an interaction between two mechanisms "
            "covered separately. Tag these MODEL_INFERENCE with honest (lower) confidence. Return ONLY one JSON object with keys findings, "
            "mechanisms, variables, hypotheses, conditions, horizons, limitations. Aim to add useful coverage rather than forcing a fixed "
            "count.\n\nEXISTING EXTRACTION:\n" +
            json.dumps(base, ensure_ascii=False)[:80000] +
            "\n\nLINE-ADDRESSED REPORT:\n" + json.dumps(parser, ensure_ascii=False)[:160000]
        )
    return (
        "You are ACE Studio's senior quantitative-finance research analyst. Analyse the COMPLETE line-addressed Markdown report "
        "and build a comprehensive research intelligence map for downstream hypothesis generation. Return ONLY ONE JSON OBJECT. "
        "Do not wrap it in an array, Markdown fences, Python dict syntax, or prose. Never invent facts, citations, empirical results, "
        "BRAIN field IDs, datasets, or operators. Distinguish SOURCE_SUPPORTED claims from MODEL_INFERENCE. Every source-supported "
        "finding/hypothesis should carry source_lines referring to the report lines. Variables are economic concepts, not BRAIN field names. "
        "Extract breadth before deduplicating: preserve separate mechanisms, conditional effects, horizons, and measurable variables when "
        "the report treats them as distinct. Aim for 10-60 useful findings, 10-50 mechanisms, 15-100 variables, and 8-30 distinct, "
        "testable hypotheses when supported by the report. Do not manufacture items just to hit a count.\n\n"
        "MOST OF THE VALUE IS IN WHAT THE REPORT DOESN'T SAY OUTRIGHT. Most reports state one or two headline hypotheses "
        "explicitly; do not stop there. For every mechanism and finding you extract, actively ask: what ELSE would have to be "
        "true if this mechanism is real, that the authors never framed as a hypothesis themselves? Look for: (a) the mechanism "
        "applied to a different horizon, conditioning variable, or subsample than the one the authors tested; (b) a secondary or "
        "control variable the report mentions in passing that plausibly drives its own testable effect; (c) an implication of a "
        "stated finding that the authors describe but never explicitly convert into a hypothesis; (d) an interaction between two "
        "mechanisms the report discusses separately. Tag every one of these MODEL_INFERENCE and set confidence honestly below what "
        "you'd give a SOURCE_SUPPORTED item — but include them; a report that yields only its explicit hypotheses has been "
        "under-mined, not fully analysed.\n\n"
        "EXACT JSON SHAPE:\n"
        '{"title":"...","research_question":"...","findings":[{"text":"...","evidence":"SOURCE_SUPPORTED","source_lines":[1]}],'
        '"mechanisms":[{"name":"...","explanation":"...","evidence":"SOURCE_SUPPORTED","source_lines":[1]}],'
        '"variables":[{"name":"...","role":"...","source_lines":[1]}],'
        '"hypotheses":[{"statement":"...","mechanism":"...","expected_sign":"positive|negative|conditional|unknown",'
        '"horizon":"...","conditions":"...","confidence":1,"evidence":"SOURCE_SUPPORTED|MODEL_INFERENCE","source_lines":[1]}],'
        '"conditions":[],"horizons":[],"limitations":[]}\n\n'
        "LINE-ADDRESSED REPORT:\n" + json.dumps(parser, ensure_ascii=False)[:160000]
    )


def _merge_research_maps(base: dict, extra: dict) -> dict:
    """Merge enrichment output while preserving provenance and avoiding text duplicates."""
    out = dict(base)
    for key, limit, field in (("findings", 80, "text"), ("mechanisms", 60, "name"), ("variables", 120, "name"), ("hypotheses", 40, "statement")):
        merged = list(out.get(key) or [])
        seen = set()
        for item in merged:
            val = item.get(field, "") if isinstance(item, dict) else str(item)
            seen.add(re.sub(r"\W+", "", str(val).lower()))
        for item in extra.get(key) or []:
            val = item.get(field, "") if isinstance(item, dict) else str(item)
            norm = re.sub(r"\W+", "", str(val).lower())
            if norm and norm not in seen:
                merged.append(item); seen.add(norm)
        out[key] = merged[:limit]
    for key in ("research_question", "conditions", "horizons", "limitations"):
        if not out.get(key) and extra.get(key): out[key] = extra[key]
    return out

def _research_llm_generate(prompt: str, *, max_tokens: int = 12000):
    """Call the explicitly configured research provider for a JSON research object."""
    from app.core.llm_router import get_chain
    chain = get_chain("research")
    if not chain:
        raise RuntimeError("No configured research LLM provider is available. Check the Research provider/model and API key in Settings.")
    provider = chain[0]
    generate_json = getattr(provider, "generate_json", None)
    if not callable(generate_json):
        raise RuntimeError(f"Research provider {getattr(provider, 'name', 'unknown')} does not support structured JSON analysis.")
    raw = generate_json(prompt, max_tokens=max_tokens)
    return _coerce_research_json(raw), getattr(provider, "name", ""), getattr(provider, "model", "")


def extract_research(md: str, *, use_llm: bool=True, region: str = "") -> tuple[dict, str, str]:
    base = _heuristic_extract(md)
    if not use_llm:
        return base, "local", "heuristic"

    from app.knowledge.service import memory_prompt_context
    mem = memory_prompt_context(f"{base.get('title','')} {(md or '')[:2000]}", region=region, limit=8)

    try:
        # Send the original Markdown report to the research model. The deterministic
        # parser is retained for provenance, but is not a substitute for the report.
        report_lines = "\n".join(f"{i + 1}: {line}" for i, line in enumerate((md or "").splitlines()))
        analysis_base = dict(base)
        analysis_base["parser"] = {"line_count": len((md or "").splitlines())}
        prompt = _research_llm_prompt(analysis_base)
        prompt = prompt.replace(
            "LINE-ADDRESSED REPORT:\n" + json.dumps(analysis_base.get("parser", {}), ensure_ascii=False)[:160000],
            "LINE-ADDRESSED REPORT:\n" + report_lines[:180000], 1)
        if mem:
            prompt = prompt + "\n\n" + mem
        obj, provider, model = _research_llm_generate(prompt, max_tokens=12000)
        analysis = _normalise_llm_research(obj, base)

        sparse = (len(analysis.get("findings", [])) < 8 or len(analysis.get("variables", [])) < 10 or
                  len(analysis.get("hypotheses", [])) < 8)
        if sparse:
            enrichment_prompt = _research_llm_prompt(analysis, enrichment=True)
            enrichment_prompt = enrichment_prompt.replace(
                "LINE-ADDRESSED REPORT:\n" + json.dumps(analysis.get("parser", {}), ensure_ascii=False)[:160000],
                "LINE-ADDRESSED REPORT:\n" + report_lines[:180000], 1)
            if mem:
                enrichment_prompt = enrichment_prompt + "\n\n" + mem
            extra, _, _ = _research_llm_generate(enrichment_prompt, max_tokens=10000)
            analysis = _merge_research_maps(analysis, _normalise_llm_research(extra, base))
            analysis["analysis_note"] = "A second enrichment pass was used because the first extraction was sparse."

        if not analysis.get("hypotheses"):
            recovery_prompt = _research_llm_prompt(analysis, recovery=True)
            recovery_prompt = recovery_prompt.replace(
                "LINE-ADDRESSED REPORT:\n" + json.dumps(analysis.get("parser", {}), ensure_ascii=False)[:160000],
                "LINE-ADDRESSED REPORT:\n" + report_lines[:180000], 1)
            if mem:
                recovery_prompt = recovery_prompt + "\n\n" + mem
            recovered, _, _ = _research_llm_generate(recovery_prompt, max_tokens=10000)
            analysis = _merge_research_maps(analysis, _normalise_llm_research(recovered, base))
            analysis["analysis_note"] = "A focused hypothesis-recovery pass was required."

        analysis["analysis_mode"] = "LLM_RESEARCH_ANALYSIS"
        analysis["provider"] = provider
        analysis["model"] = model
        return analysis, provider, model
    except Exception as e:
        base["analysis_mode"] = "HEURISTIC_FALLBACK"
        base["analysis_error"] = str(e)[:500]
        return base, "local", "heuristic"


def save_extraction(doc_id:int, extraction:dict, status="analyzed"):
    with SessionLocal() as db:
        row=db.get(M.ResearchDocument,doc_id)
        if not row: raise ValueError("research document not found")
        row.extraction_json=json.dumps(extraction,ensure_ascii=False)
        row.status=status
        db.commit()


def list_documents(limit=50):
    with SessionLocal() as db:
        rows=db.scalars(select(M.ResearchDocument).order_by(M.ResearchDocument.created_at.desc()).limit(limit)).all()
        return [{"id":r.id,"title":r.title,"source":r.source,"status":r.status,"created_at":r.created_at,
                 "chars":len(r.content),"extraction":_safe_json(r.extraction_json,{})} for r in rows]


def _norm_tokens(s:str):
    return set(re.findall(r"[a-z0-9_]+",(s or "").lower()))


def _concept_text(extraction: dict) -> str:
    concepts = []
    for h in extraction.get("hypotheses", []) or []:
        if isinstance(h, dict): concepts += [str(h.get("statement", "")), str(h.get("mechanism", "")), str(h.get("conditions", ""))]
    for x in extraction.get("variables", []) or []:
        concepts.append(str(x.get("name") or x.get("variable") or x.get("concept") or "") if isinstance(x, dict) else str(x))
    for x in extraction.get("mechanisms", []) or []:
        concepts.append(str(x.get("name") or x.get("explanation") or "") if isinstance(x, dict) else str(x))
    return " ".join(concepts)

def map_fields(extraction:dict, fields:list[dict], top_k:int=8) -> list[dict]:
    corpus=_norm_tokens(_concept_text(extraction))
    out=[]
    for f in fields:
        text=f"{f.get('id','')} {f.get('description','')} {f.get('category','')} {f.get('category_name','')}"
        toks=_norm_tokens(text); id_tokens=_norm_tokens(f.get('id',''))
        overlap=len(corpus & toks); score=overlap + 0.75*len(corpus & id_tokens)
        out.append({"field":f,"score":round(score,3),"matched_terms":sorted(corpus&toks)[:12]})
    out.sort(key=lambda x:(-x["score"],x["field"].get("id", "")))
    return out[:top_k]

def auto_map_fields(extraction: dict, *, region: str="IND", delay: int=1, universe: str="TOP3000",
                    instrument: str="EQUITY", top_k: int=16, max_datasets_per_hypothesis: int=6, progress=None) -> dict:
    """Automatically scan the BRAIN catalogue and return hypothesis-specific field matches.

    The catalogue is scanned globally for the requested context. The per-hypothesis dataset cap only
    limits which datasets are expanded into fields; it is not a user-selected dataset limit.
    """
    # Auto-map is deliberately catalogue-only. Data Explorer is the single BRAIN ingestion
    # path and persists datasets/fields through knowledge.ingest_datasets()/ingest_fields().
    # Do not re-query BRAIN here.
    eff_region = str(region or "IND").strip().upper()
    eff_delay = int(delay)
    eff_universe = str(universe or "TOP3000").strip()
    eff_instrument = str(instrument or "EQUITY").strip().upper()

    datasets = knowledge_service.catalogue_datasets(
        eff_region, eff_delay, eff_instrument, eff_universe
    )
    hypotheses = extraction.get("hypotheses", []) or []
    if not hypotheses:
        return {"matches": [], "datasets_scanned": len(datasets), "matched_datasets": 0, "fields_scanned": 0,
                "region": eff_region, "delay": eff_delay, "universe": eff_universe,
                "instrument": eff_instrument, "catalogue_ready": bool(datasets)}
    if not datasets:
        return {
            "matches": [], "datasets_scanned": 0, "matched_datasets": 0, "fields_scanned": 0,
            "region": eff_region, "delay": eff_delay, "universe": eff_universe,
            "instrument": eff_instrument, "catalogue_ready": False,
            "catalogue_message": (
                "No local BRAIN datasets are catalogued for this region/delay/universe/instrument. "
                "Refresh Data Explorer for this configuration before running Auto-map."
            ),
        }

    from app.research import service as research_service
    ranked=[]
    for h in hypotheses:
        q=" ".join(str(h.get(k, "")) for k in ("statement", "mechanism", "conditions")) if isinstance(h,dict) else str(h)
        ds=sorted(datasets,key=lambda d:research_service._catalogue_score(q,d),reverse=True)
        ranked.append((h,ds[:max_datasets_per_hypothesis]))
    ids=list(dict.fromkeys(str(d.get("id")) for _,ds in ranked for d in ds if d.get("id")))
    if progress: progress(message=f"catalogue scan: {len(datasets)} datasets → {len(ids)} candidate datasets")
    if not ids: return {"matches":[],"datasets_scanned":len(datasets),"matched_datasets":0,"fields_scanned":0,"region":eff_region,"delay":eff_delay,"universe":eff_universe}
    rows = knowledge_service.catalogue_fields(ids, eff_region, eff_delay)
    matches=[]
    for h,ds in ranked:
        q=" ".join(str(h.get(k,"")) for k in ("statement","mechanism","conditions")) if isinstance(h,dict) else str(h)
        dsids={d.get("id") for d in ds}
        fs=[f for f in rows if f.get("dataset_id") in dsids]
        scored=map_fields({"hypotheses":[h]},fs,top_k=max(1,top_k))
        for m in scored:
            m["hypothesis"] = h
            m["datasets"] = [{"id":d.get("id"),"name":d.get("name"),"score":round(research_service._catalogue_score(q,d),4)} for d in ds]
            matches.append(m)
    matches.sort(key=lambda x:(-x.get("score",0), str(x.get("field",{}).get("id",""))))
    result = {"matches":matches,"datasets_scanned":len(datasets),"matched_datasets":len(ids),"fields_scanned":len(rows),
              "region":eff_region,"delay":eff_delay,"universe":eff_universe,"instrument":eff_instrument,
              "catalogue_ready": True}
    if not rows:
        result["catalogue_fields_missing"] = True
        result["catalogue_message"] = (
            "Candidate datasets are catalogued, but their fields are not present locally. "
            "Refresh Data Explorer Fields for the selected datasets before running Auto-map."
        )
    return result


def create_experiment(name, region, delay, universe, research_ids, hypothesis, field_ids, notes="") -> int:
    with SessionLocal() as db:
        row=M.Experiment(name=name or "Untitled research experiment", region=region, delay=int(delay), universe=universe,
                         research_ids_json=json.dumps(research_ids or []), hypothesis_json=json.dumps(hypothesis or {}),
                         field_ids_json=json.dumps(field_ids or []), notes=notes or "")
        db.add(row); db.commit(); return row.id


def list_experiments(limit=50):
    with SessionLocal() as db:
        rows=db.scalars(select(M.Experiment).order_by(M.Experiment.created_at.desc()).limit(limit)).all()
        ids=[r.id for r in rows]
        # sim_results.experiment_id links back here once candidates are sent to Simulation and
        # actually run — lets the UI show real outcomes, not just "N candidates generated".
        sim_counts={}
        if ids:
            agg=db.execute(select(M.SimResult.experiment_id, func.count(), func.sum(M.SimResult.passed_gate))
                           .where(M.SimResult.experiment_id.in_(ids)).group_by(M.SimResult.experiment_id)).all()
            sim_counts={eid: {"simulated": cnt, "passed": int(passed or 0)} for eid, cnt, passed in agg}
        return [{"id":r.id,"name":r.name,"status":r.status,"region":r.region,"delay":r.delay,"universe":r.universe,
                 "research_ids":_safe_json(r.research_ids_json,[]),"hypothesis":_safe_json(r.hypothesis_json,{}),
                 "field_ids":_safe_json(r.field_ids_json,[]),"expressions":_safe_json(r.expression_json,[]),
                 "results":_safe_json(r.results_json,[]),"notes":r.notes,"created_at":r.created_at,
                 **sim_counts.get(r.id,{"simulated":0,"passed":0})} for r in rows]


def alpha_dna(expression:str, region:str="", categories:dict|None=None) -> dict:
    from app.generation import service as gen
    fields=gen._leaf_idents(expression)
    ops=gen._operators_in(expression)
    cats=list(dict.fromkeys(categories.get(f) for f in fields if categories and categories.get(f))) if categories else []
    structure={"n_fields":len(fields),"n_operators":len(ops),"depth":max(1,expression.count("(") ),
               "families":sorted(set(o.split("_")[0] if "_" in o else o for o in ops))}
    return {"expression":expression,"expression_key":canonical(expression),"region":region,"fields":fields,
            "operators":ops,"categories":cats,"structure":structure}


def save_dna(d:dict):
    with SessionLocal() as db:
        row=db.scalar(select(M.AlphaDNA).where(M.AlphaDNA.expression_key==d["expression_key"]))
        if not row:
            row=M.AlphaDNA(expression_key=d["expression_key"],expression=d["expression"],region=d.get("region",""),
                           fields_json=json.dumps(d.get("fields",[])),operators_json=json.dumps(d.get("operators",[])),
                           categories_json=json.dumps(d.get("categories",[])),structure_json=json.dumps(d.get("structure",{})))
            db.add(row)
        else:
            row.expression=d["expression"]
        db.commit(); return row.id


def record_failure(expression, region, reason, details=None, experiment_id=0):
    with SessionLocal() as db:
        db.add(M.ResearchFailure(expression=expression,region=region,reason=reason,details_json=json.dumps(details or {}),experiment_id=experiment_id))
        db.commit()


def field_intelligence(region:str="", limit:int=50):
    with SessionLocal() as db:
        rows=db.scalars(select(M.FieldInsight).where(M.FieldInsight.region==region).order_by(M.FieldInsight.passed_uses.desc()).limit(limit)).all()
        return [{"field_id":r.field_id,"region":r.region,"category":r.category,"uses":r.uses,"valid_uses":r.valid_uses,
                 "passed_uses":r.passed_uses,"failed_uses":r.failed_uses,"avg_sharpe":(r.sum_sharpe/r.valid_uses if r.valid_uses else 0),
                 "avg_fitness":(r.sum_fitness/r.valid_uses if r.valid_uses else 0),
                 "successful_operators":_safe_json(r.successful_operators_json,[]),"common_partners":_safe_json(r.common_partners_json,[])} for r in rows]


def dna_list(limit=100, region=""):
    with SessionLocal() as db:
        q=select(M.AlphaDNA).order_by(M.AlphaDNA.created_at.desc()).limit(limit)
        if region: q=q.where(M.AlphaDNA.region==region)
        rows=db.scalars(q).all()
        return [{"id":r.id,"expression":r.expression,"region":r.region,"fields":_safe_json(r.fields_json,[]),"operators":_safe_json(r.operators_json,[]),
                 "categories":_safe_json(r.categories_json,[]),"structure":_safe_json(r.structure_json,{}) ,"novelty":r.novelty,
                 "robustness":r.robustness,"best_sharpe":r.best_sharpe,"best_fitness":r.best_fitness,"pass_count":r.pass_count,"fail_count":r.fail_count} for r in rows]


def mutate_expressions(expressions:list[str], fields:list[dict], max_operators:int=4, max_results:int=60) -> list[dict]:
    """Deterministic, validator-gated mutations. This is deliberately conservative: it changes
    structure/window around a known expression instead of inventing fields."""
    from app.generation import service as gen
    out=[]; seen=set()
    for expr in expressions:
        candidates=[
            f"rank({expr})", f"scale({expr})", f"winsorize({expr})",
            f"ts_rank({expr}, 20)", f"ts_rank({expr}, 63)", f"ts_rank({expr}, 126)",
            f"ts_decay_linear({expr}, 20)", f"ts_decay_linear({expr}, 60)",
            f"ts_delta({expr}, 20)", f"ts_delta({expr}, 63)",
        ]
        val=gen._validator(fields,max_operators=max_operators,multi_field=True)
        for c in candidates:
            k=canonical(c)
            if k in seen: continue
            try:
                r=val.validate(c); extra=gen.operator_issues(c,gen._param_specs()) if r.ok else []
                if r.ok and not extra:
                    seen.add(k); out.append({"expression":c,"parent":expr,"mutation":c[len(expr)+1:].split("(")[0] if c.startswith("rank(") else "transform"})
            except Exception: pass
            if len(out)>=max_results: return out
    return out
