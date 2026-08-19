import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";
import "./templates.css";

interface Op { name: string; params: string[]; }
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);

// The starting example switches with the mode so the placeholders always match: one {field}
// for single-field, {field} + {field2} for multi-field.
const SINGLE_EX = "group_zscore(ts_rank({field}, 60), subindustry)";
const MULTI_EX = "group_zscore(ts_regression({field}, {field2}, 120), subindustry)";

// Structural check (not a full parser): unbalanced brackets, empty call, dangling comma,
// missing {field} slot.
function checkExpr(e: string): string[] {
  const iss: string[] = []; let depth = 0;
  for (const ch of e) { if (ch === "(") depth++; else if (ch === ")") { depth--; if (depth < 0) { iss.push("A ')' has no matching '('"); break; } } }
  if (depth > 0) iss.push(depth + " unclosed '('");
  if (/\(\s*\)/.test(e)) iss.push("Empty ( ) — missing input");
  if (/,\s*\)/.test(e) || /\(\s*,/.test(e) || /,\s*,/.test(e)) iss.push("Stray comma");
  if (!/\{field\d*(:\w+)?\}/.test(e)) iss.push("No {field} slot");
  return iss;
}

export function TemplateStudio() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();

  // Persisted so leaving and returning (or a reload) keeps the template, mode and results.
  const [tmpl, setTmpl] = usePersistentState("tpl:text", SINGLE_EX);
  const [vecOps, setVecOps] = usePersistentState<string[]>("tpl:vecops", ["vec_avg", "vec_max", "vec_min", "vec_norm"]);
  const [multi, setMulti] = usePersistentState("tpl:multi", false);
  const [maxOps, setMaxOps] = usePersistentState("tpl:maxops", 4);
  const [field2Src, setField2Src] = usePersistentState("tpl:field2src", "same");   // same | <datasetId> | custom
  const [customFields, setCustomFields] = usePersistentState("tpl:customfields", "");
  const [idea, setIdea] = usePersistentState("tpl:idea", "");   // used by Suggest From Data when no fields are selected — the LLM picks the catalogue fields itself
  const [out, setOut] = usePersistentState<any | null>("tpl:out", null);

  const [ops, setOps] = useState<Op[]>([]);
  const [busy, setBusy] = useState(false);       // Preview & validate
  const [busyS, setBusyS] = useState(false);     // Suggest from data
  // Datasets to retry with, set when a near-miss result is reused as a template.
  const [retryDs, setRetryDs] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("ace2:tpl:retry") || "[]"); } catch { return []; } });
  const taRef = useRef<HTMLTextAreaElement>(null);

  // ── operator autocomplete ────────────────────────────────────────────────────────
  const [ac, setAc] = useState<{ open: boolean; matches: Op[]; idx: number; start: number; end: number }>(
    { open: false, matches: [], idx: 0, start: 0, end: 0 });

  useEffect(() => { api.get<{ operators: Op[] }>("/generate/operators").then((d) => setOps(d.operators || [])); }, []);
  const VEC = ["vec_avg", "vec_max", "vec_min", "vec_norm", "vec_sum", "vec_count", "vec_stddev", "vec_range"];

  const selFields = R.fields.filter((f) => R.selFields.includes(f.id));
  const dsById = Object.fromEntries(R.datasets.map((d) => [d.id, d]));
  const fieldCats: Record<string, string> = {};
  selFields.forEach((f) => { const c = f.dataset_id && dsById[f.dataset_id]?.category_id; if (c) fieldCats[f.id] = c; });
  const nVec = selFields.filter((f) => String(f.type).toUpperCase() === "VECTOR").length;

  // {field2} pool: the same selected fields, a specific selected dataset's fields, or the user's own field ids.
  const selectedDatasets = R.datasets.filter((d) => R.selDatasets.includes(d.id));
  const customList = customFields.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean);
  const field2Ids =
    field2Src === "custom" ? customList :
    field2Src === "same" ? selFields.map((f) => f.id) :
    selFields.filter((f) => f.dataset_id === field2Src).map((f) => f.id);
  // All fields the validator must know (selected + any custom, typed MATRIX by default).
  const allFieldsPayload = [
    ...selFields.map((f) => ({ id: f.id, type: f.type })),
    ...customList.map((id) => ({ id, type: "MATRIX" })),
  ];

  const syntax = useMemo(() => {
    const ls = lines(tmpl); let bad = 0; const detail: string[] = [];
    ls.forEach((e, i) => { const is = checkExpr(e); if (is.length) { bad++; if (detail.length < 2) detail.push(`Line ${i + 1}: ${is[0]}`); } });
    return { bad, total: ls.length, detail };
  }, [tmpl]);

  // Rank matches: exact-prefix first (shortest first), then operators that merely contain it.
  function computeMatches(token: string): Op[] {
    const t = token.toLowerCase();
    const pre = ops.filter((o) => o.name.toLowerCase().startsWith(t));
    const mid = ops.filter((o) => !o.name.toLowerCase().startsWith(t) && o.name.toLowerCase().includes(t));
    pre.sort((a, b) => a.name.length - b.name.length || a.name.localeCompare(b.name));
    mid.sort((a, b) => a.name.localeCompare(b.name));
    return [...pre, ...mid].slice(0, 40);
  }

  // After any edit / caret move, recompute the token under the caret and the suggestion list.
  function refreshAc(ta: HTMLTextAreaElement) {
    const pos = ta.selectionStart;
    if (pos !== ta.selectionEnd) return setAc((a) => ({ ...a, open: false }));
    const m = ta.value.slice(0, pos).match(/([A-Za-z_][A-Za-z0-9_]*)$/);
    if (!m) return setAc((a) => ({ ...a, open: false }));
    const token = m[1], start = pos - token.length;
    const matches = computeMatches(token);
    setAc({ open: matches.length > 0, matches, idx: 0, start, end: pos });
  }

  function insertOp(op: Op) {
    const ta = taRef.current; if (!ta) return;
    const ins = op.name + "(";
    const v = tmpl.slice(0, ac.start) + ins + tmpl.slice(ac.end);
    setTmpl(v);
    const caret = ac.start + ins.length;
    setAc((a) => ({ ...a, open: false }));
    requestAnimationFrame(() => { ta.focus(); ta.selectionStart = ta.selectionEnd = caret; });
  }

  function onKeyDown(ev: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (!ac.open) {
      if (ev.key === "Tab" && !ev.shiftKey) { const ta = ev.currentTarget; refreshAc(ta); }
      return;
    }
    if (ev.key === "ArrowDown") { ev.preventDefault(); setAc((a) => ({ ...a, idx: (a.idx + 1) % a.matches.length })); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); setAc((a) => ({ ...a, idx: (a.idx - 1 + a.matches.length) % a.matches.length })); }
    else if (ev.key === "Enter" || ev.key === "Tab") { ev.preventDefault(); insertOp(ac.matches[ac.idx]); }
    else if (ev.key === "Escape") { ev.preventDefault(); setAc((a) => ({ ...a, open: false })); }
  }

  // Switching the mode swaps in the matching example (only if the text is still an untouched
  // example, so a user's own work is never overwritten).
  function toggleMulti() {
    setMulti((was) => {
      const now = !was;
      setTmpl((cur) => (cur.trim() === SINGLE_EX || cur.trim() === MULTI_EX || !cur.trim())
        ? (now ? MULTI_EX : SINGLE_EX) : cur);
      return now;
    });
  }

  async function expand() {
    if (!selFields.length) return toast("Select datafields in the Data Explorer first.", "warn");
    if (syntax.bad) return toast(`Fix the template first — ${syntax.detail[0]}.`, "warn");
    setBusy(true);
    const d = await api.post<any>("/generate/templates/expand", {
      templates: lines(tmpl), field_ids: selFields.map((f) => f.id),
      field2_ids: multi ? field2Ids : [],
      fields: allFieldsPayload, vec_ops: vecOps,
      max_operators: maxOps, multi_field: multi,
    });
    setBusy(false);
    if (d.error) return toastErr(d.error);
    setOut(d);
    toast(`${d.report.valid} valid of ${d.report.total} expansions.`);
  }

  async function suggest() {
    if (!selFields.length && !idea.trim()) return toast("Select datafields, or describe the idea below so the LLM can pick the data itself.", "warn");
    setBusyS(true);
    const start = await api.post<any>("/generate/templates/suggest", {
      region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument, universe: R.ctx.universe,
      dataset_names: R.datasets.filter((d) => R.selDatasets.includes(d.id)).map((d) => d.name || d.id),
      fields: selFields.map((f) => ({ id: f.id, type: f.type, description: f.description })),
      categories: fieldCats, max_operators: maxOps, n: 8, multi_field: multi, idea,
    });
    if (start.error || !start.job_id) { setBusyS(false); return toastErr(start.error || "Could not start."); }
    let s: any = {};
    for (; ;) { s = await api.get(`/generate/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setBusyS(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    const t = (s.result?.templates || []);
    if (t.length) { setTmpl(t.join("\n")); toast(`${t.length} templates suggested via ${s.result.provider}.`); }
    else toast("No templates returned.", "warn");
  }

  const valid = (out?.results || []).filter((r: any) => r.ok).map((r: any) => r.expr);

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      {/* ── editor ── */}
      <div className="panel tpl-panel">
        <div className="dx-head">
          <b>Template</b>
          <span className="tpl-tag">{"{field}"} single · {"{field2}"} multi · type an operator, Tab / ↑↓ + Enter</span>
        </div>
        <div className="tpl-body">
          {retryDs.length ? (
            <div className="mut" style={{ fontSize: 12, color: "var(--acc)", background: "var(--acc-weak)", padding: "8px 10px", borderRadius: 8, marginBottom: 8 }}>
              ⟳ Reused from a near-miss. Retry with dataset id(s): <b>{retryDs.join(", ")}</b> — fetch them in the Data Explorer and select their fields, then Preview.
              <span className="pill sm" style={{ marginLeft: 8 }} onClick={() => { localStorage.removeItem("ace2:tpl:retry"); setRetryDs([]); }}>dismiss</span>
            </div>
          ) : null}
          <div className="tpl-editor">
            <textarea
              ref={taRef} value={tmpl} spellCheck={false}
              onChange={(e) => { setTmpl(e.target.value); refreshAc(e.currentTarget); }}
              onKeyDown={onKeyDown}
              onKeyUp={(e) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) refreshAc(e.currentTarget); }}
              onBlur={() => setTimeout(() => setAc((a) => ({ ...a, open: false })), 120)}
            />
            {ac.open ? (
              <div className="tpl-ac">
                {ac.matches.map((o, i) => (
                  <div key={o.name} className={"tpl-ac-item" + (i === ac.idx ? " on" : "")}
                    onMouseEnter={() => setAc((a) => ({ ...a, idx: i }))}
                    onMouseDown={(e) => { e.preventDefault(); insertOp(o); }}>
                    <span>{o.name}</span>
                    <span className="sig">({(o.params || []).join(", ")})</span>
                  </div>
                ))}
                <div className="tpl-ac-hint">↑↓ to move · Enter / Tab to insert · Esc to dismiss</div>
              </div>
            ) : null}
          </div>

          <div style={{ fontSize: 12, marginTop: 6, color: syntax.bad ? "var(--bad)" : "var(--ok)" }}>
            {syntax.bad ? `⚠ ${syntax.bad} of ${syntax.total} need a fix — ${syntax.detail.join("; ")}` : `✓ ${syntax.total} template(s) look OK`}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 10, alignItems: "end", marginTop: 12,
            padding: "10px 12px", border: "1px solid var(--line)", borderRadius: "var(--radius-sm)", background: "var(--surface-2)" }}>
            <div>
              <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 4 }}>Mode</div>
              <div className="dx-filters">
                <span className={"pill" + (!multi ? " on" : "")} onClick={() => multi && toggleMulti()}>Single {"{field}"}</span>
                <span className={"pill" + (multi ? " on" : "")} onClick={() => !multi && toggleMulti()}>Multi {"{field}"} + {"{field2}"}</span>
              </div>
            </div>
            <label className="fld" style={{ width: 88 }}><span>Max Ops</span>
              <NumberInput min={1} fallback={4} value={maxOps} onChange={setMaxOps} /></label>

            {multi ? <div style={{ gridColumn: "1 / -1" }}>
              <label className="fld"><span>{"{field2}"} source</span>
                <select value={field2Src} onChange={(e) => setField2Src(e.target.value)}>
                  <option value="same">Same selected fields</option>
                  {selectedDatasets.map((d) => <option key={d.id} value={d.id}>Dataset · {d.name || d.id}</option>)}
                  <option value="custom">My own field ids…</option>
                </select></label>
              {field2Src === "custom" ?
                <label className="fld" style={{ marginTop: 6 }}><span>Your field ids (comma / space separated)</span>
                  <input value={customFields} onChange={(e) => setCustomFields(e.target.value)} placeholder="anl4_xxx, fnd6_yyy" /></label> : null}
              <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>{"{field}"} uses your selected fields · {"{field2}"} draws from {field2Ids.length} field(s).</div>
            </div> : null}
          </div>

          <div className="mut" style={{ fontSize: 11, marginTop: 10, marginBottom: 4 }}>
            Vector operators — VECTOR fields ({nVec} selected) are wrapped in one of these
          </div>
          <div className="dx-filters wrap">
            {VEC.map((v) => <span key={v} className={"pill" + (vecOps.includes(v) ? " on" : "")}
              onClick={() => setVecOps((x) => x.includes(v) ? x.filter((y) => y !== v) : [...x, v])}>{v}</span>)}
          </div>

          {!selFields.length ? <label className="fld" style={{ marginTop: 10 }}><span>Idea (No Fields Selected — The LLM Picks The Data)</span>
            <input value={idea} onChange={(e) => setIdea(e.target.value)} placeholder="e.g. earnings surprise momentum that fades within a week" /></label> : null}

          <div className="dx-filters" style={{ marginTop: 12 }}>
            <button className="btn" onClick={expand} disabled={busy || busyS}>
              {busy ? <><span className="spin" /> Expanding…</> : "Preview & Validate"}</button>
            <button className="btn ghost" onClick={suggest} disabled={busy || busyS}>
              {busyS ? <><span className="spin" /> Suggesting…</> : "✦ Suggest From Data"}</button>
          </div>
        </div>
      </div>

      {/* ── expansions (header sticks; only the table scrolls) ── */}
      <div className="panel tpl-panel">
        <div className="dx-head">
          <b>Expansions</b>
          {out ? <span className="mut">{out.report.valid} valid · {out.report.rejected} rejected</span> : null}
          {valid.length ? <button className="btn sm" style={{ marginLeft: "auto" }}
            onClick={() => { R.setPendingExperimentId(null); R.setPending(valid); nav("/simulate"); }}>Send {valid.length} To Simulate →</button> : null}
        </div>
        <div className="tpl-body flush">
          {!out ? <div className="empty">Select fields, write a template, and Preview. VECTOR fields fan out across your vec_* choices; multi-field takes a bounded product.</div> :
            <table><thead><tr><th></th><th>Expansion</th><th>Issues</th></tr></thead>
              <tbody>{out.results.map((r: any, i: number) => (
                <tr key={i}><td><span className={"badge " + (r.ok ? "ok" : "bad")}>{r.ok ? "ok" : "rej"}</span></td>
                  <td><code style={{ fontSize: 11 }}>{r.expr}</code></td>
                  <td className="mut" style={{ fontSize: 11 }}>{(r.issues || []).join(", ")}</td></tr>))}</tbody></table>}
        </div>
      </div>
    </div>
  );
}
