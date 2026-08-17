import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";
import "../data/data.css";

const num = (v: any) => (v == null ? "—" : (+v).toFixed(2));
const lines = (s: string) => s.split("\n").map((x) => x.trim()).filter(Boolean);
const intOr = (v: any, d: number) => { const n = parseInt(v, 10); return Number.isFinite(n) ? n : d; };
const OWN = /(?<![\w."'])own(?![\w."'])/;

// SuperAlpha — its own separated workspace with its OWN market context (region / delay /
// instrument), chosen here rather than inherited, so you can build super alphas for a
// different region than your research context. Everything persists across navigation and
// reloads; a running simulation reconnects. Left: build & COUNT selections (a SuperAlpha
// needs >=10 component alphas). Right: combos + settings + run, same success gate.
export function SuperAlpha() {
  const R = useResearch();
  const { toast, toastErr } = useToast();

  // Local market context — defaults to the research context but is independently editable.
  const [sInst, setSInst] = usePersistentState("super:inst", R.ctx.instrument);
  const [sRegion, setSRegion] = usePersistentState("super:region", R.ctx.region);
  const [sDelay, setSDelay] = usePersistentState("super:delay", R.ctx.delay);

  const [own, setOwn] = usePersistentState("super:own", true);
  const [sel, setSel] = usePersistentState("super:sel", "own && turnover < 0.2 && datafield_count <= 3");
  const [combo, setCombo] = usePersistentState("super:combo", "1\nstats = generate_stats(alpha); ts_ir(stats.returns, 500)");
  const [tpl, setTpl] = usePersistentState("super:tpl", "");
  const [vars, setVars] = usePersistentState("super:vars", "");
  const [selLimit, setSelLimit] = usePersistentState("super:sellimit", 1000);
  const [unis, setUnis] = usePersistentState<string[]>("super:unis", ["ILLIQUID_MINVOL1M"]);
  const [neuts, setNeuts] = usePersistentState<string[]>("super:neuts", ["FAST"]);
  const [gate, setGate] = usePersistentState("super:gate", { sharpe: 1.58, fitness: 1.0 });
  const [conc, setConc] = usePersistentState("super:conc", 3);
  const [res, setRes] = usePersistentState<any | null>("super:res", null);
  const [jobId, setJobId] = usePersistentState<string>("super:job", "");

  const [tag, setTag] = useState(() => localStorage.getItem("ace2-tag") || "");
  const [winnerTag, setWinnerTag] = useState(() => localStorage.getItem("ace2-winnertag") || "");
  const [cntOut, setCntOut] = useState<any | null>(null);
  const [cntBusy, setCntBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyExp, setBusyExp] = useState(false);
  const [busySugS, setBusySugS] = useState(false);
  const [busySugC, setBusySugC] = useState(false);
  const [comboChk, setComboChk] = useState<any[] | null>(null);
  const [comboChkBusy, setComboChkBusy] = useState(false);

  async function checkCombos() {
    if (!lines(combo).length) return toast("Write a combo expression first.", "warn");
    setComboChkBusy(true);
    const d = await api.post<any>("/super/validate", { selections: [], combos: lines(combo) });
    setComboChkBusy(false);
    if (d.error) return toastErr(d.error);
    setComboChk(d.combo || []);
  }
  const [prog, setProg] = useState("");
  const [view, setView] = useState<"res" | "vars">("res");
  const [vocab, setVocab] = useState<any>({ selection_variables: [], selection_non_variables: [] });
  const jobRef = useRef(jobId); jobRef.current = jobId;

  const regions = R.regions(sInst);
  const delays = R.delays(sInst, sRegion);
  const universes = R.universes(sInst, sRegion, sDelay);
  const neutOpts = useMemo(() => [...new Set((R.options || []).filter((o) => o.instrument === sInst && o.region === sRegion).flatMap((o) => o.neutralizations))], [R.options, sInst, sRegion]);

  useEffect(() => { api.get<any>("/super/vocab").then(setVocab); }, []);
  const gateDelay = useRef<number | null>(null);
  useEffect(() => {
    if (gateDelay.current === sDelay) return;
    gateDelay.current = sDelay;
    api.get<any>(`/simulate/gate?delay=${sDelay}`).then((g) => setGate({ sharpe: g.sharpe, fitness: g.fitness }));
  }, [sDelay]);
  // Keep the chosen universes valid for the local region/delay/instrument (drop invalid ones;
  // if none remain, snap to a real universe) so a mismatch never breaks fetch/suggest/simulate.
  useEffect(() => {
    setUnis((prev) => {
      const keep = prev.filter((u) => universes.includes(u));
      return keep.length ? keep : (universes[0] ? [universes[0]] : prev);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sRegion, sInst, sDelay, R.options.length]);

  // Reconnect a running SuperAlpha simulation on return / reload.
  useEffect(() => { if (jobId) { setBusy(true); pollRun(jobId); } /* eslint-disable-next-line */ }, []);

  const applyOwn = (arr: string[]) => own ? arr.map((e) => OWN.test(e) ? e : `own && ${e}`) : arr;
  const toggle = (arr: string[], v: string, set: (x: string[]) => void) => set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);

  function parseVars() {
    const out: Record<string, string[]> = {};
    vars.split("\n").forEach((l) => { const i = l.indexOf("="); if (i < 1) return; const name = l.slice(0, i).trim(); const vals = l.slice(i + 1).split(",").map((s) => s.trim()).filter(Boolean); if (name && vals.length) out[name] = vals; });
    return out;
  }
  async function expand() {
    if (!tpl.trim()) return toast("Write a selection template with {name} placeholders.", "warn");
    setBusyExp(true);
    const d = await api.post<any>("/super/expand", { templates: [tpl], variables: parseVars(), paired: [] });
    setBusyExp(false);
    if (d.error) return toastErr(d.error);
    setSel([...new Set([...lines(sel), ...applyOwn(d.expressions)])].join("\n"));
    toast(`Added ${d.expressions.length} selection(s).`);
  }
  async function suggest(kind: "selection" | "combo") {
    const setB = kind === "selection" ? setBusySugS : setBusySugC; setB(true);
    try {
      const start = await api.post<any>("/super/suggest", { kind, region: sRegion, delay: sDelay, instrument: sInst, universe: unis[0] || universes[0] || "TOP3000", n: 8, own });
      if (start.error || !start.job_id) return toastErr(start.error || "Could not start.");
      let s: any = {}; for (; ;) { s = await api.get(`/super/jobs/${start.job_id}`); if (s.error || s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
      if (s.status !== "done") return toastErr(s.error || `Suggest ${s.status || "failed"}.`);
      const r = s.result || {};
      // Never silently no-op: guard a missing/empty list (LLM returned nothing or all rejected).
      const got: string[] = Array.isArray(r.expressions) ? r.expressions : [];
      const rejected = Array.isArray(r.rejected) ? r.rejected.length : 0;
      if (!got.length) {
        return toastErr(rejected
          ? `No valid ${kind}s — the model returned ${rejected} but all were rejected as invalid. Try again.`
          : `The model returned no ${kind}s. Check your LLM key/quota in Settings, then retry.`);
      }
      if (kind === "selection") setSel([...new Set([...lines(sel), ...applyOwn(got)])].join("\n"));
      else setCombo([...new Set([...lines(combo), ...got])].join("\n"));
      toast(`Added ${got.length} ${kind}(s) via ${r.provider || "LLM"}${rejected ? ` · ${rejected} rejected` : ""}.`);
    } finally {
      setB(false);
    }
  }

  async function count() {
    if (!lines(sel).length) return toast("Write selection expressions first.", "warn");
    setCntBusy(true); setCntOut(null);
    const start = await api.post<any>("/super/selection/preview", { selections: lines(sel), region: sRegion, delay: sDelay, instrument: sInst, selection_limit: selLimit });
    if (start.error || !start.job_id) { setCntBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/super/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setCntBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    setCntOut(s.result);
    toast(`${s.result.usable} of ${s.result.checked} selection(s) can build a SuperAlpha.`, s.result.usable ? "ok" : "warn");
  }
  function keepUsable() {
    const keep = (cntOut?.results || []).filter((r: any) => r.usable).map((r: any) => r.selection);
    if (keep.length) { setSel(keep.join("\n")); toast(`Kept ${keep.length} usable.`); }
  }

  async function pollRun(id: string) {
    let s: any = {};
    for (; ;) { s = await api.get(`/super/jobs/${id}`); if (s.error && s.status === undefined) break; if (s.message) setProg(`${s.message}${s.total ? ` · ${s.done}/${s.total}` : ""}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setBusy(false); setProg(""); setJobId("");
    if (s.status === "error") return toastErr(s.error);
    if (!s.result) return;
    setRes(s.result);
    if (s.result.passed) toast(`${s.result.passed}/${s.result.simulated} passed the gate.`, "ok");
    else toast(`${s.result.simulated} simulated · ${s.result.failed || 0} failed · none passed.`, "warn");
  }
  async function run() {
    if (!lines(sel).length || !lines(combo).length) return toast("Need selections and combos.", "warn");
    if (!unis.length || !neuts.length) return toast("Pick a universe and neutralization.", "warn");
    localStorage.setItem("ace2-tag", tag); localStorage.setItem("ace2-winnertag", winnerTag);
    setBusy(true); setRes(null); setView("res"); setProg("starting…");
    const start = await api.post<any>("/super/simulate", {
      selections: lines(sel), combos: lines(combo), region: sRegion, delay: sDelay,
      instrument: sInst, universes: unis, neutralizations: neuts, selection_limit: selLimit,
      concurrency: conc, min_sharpe: gate.sharpe, min_fitness: gate.fitness, tag, winner_tag: winnerTag,
    });
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
    setJobId(start.job_id); pollRun(start.job_id);
  }
  async function stop() { if (jobRef.current) await api.post(`/super/jobs/${jobRef.current}/cancel`); }

  return (
    <div className="dx" style={{ flex: 1, minHeight: 0 }}>
      {/* local market context for SuperAlpha */}
      <div className="panel dx-ctx">
        <div className="dx-ctx-row">
          <label className="fld"><span>Instrument</span>
            <select value={sInst} onChange={(e) => setSInst(e.target.value)}>{R.instruments().map((i) => <option key={i}>{i}</option>)}</select></label>
          <label className="fld"><span>Region (Independent Of Research)</span>
            <select value={sRegion} onChange={(e) => setSRegion(e.target.value)}>{regions.map((r) => <option key={r}>{r}</option>)}</select></label>
          <label className="fld"><span>Delay</span>
            <select value={String(sDelay)} onChange={(e) => setSDelay(intOr(e.target.value, 1))}>{delays.map((d) => <option key={d} value={String(d)}>{d}</option>)}</select></label>
          <label className="fld"><span>Selection Limit (How Many To Count)</span>
            <NumberInput min={1} fallback={1000} value={selLimit} onChange={setSelLimit} /></label>
          {busy ? <span className="badge ok" style={{ alignSelf: "end" }}>running</span> : null}
        </div>
      </div>

      <div className="dx-split">
        {/* build & count selections */}
        <div className="panel dx-panel">
          <div className="dx-head"><b>1 · Selections</b>
            <span className="mut">Which of your alphas take part</span>
            <span className={"pill" + (own ? " on" : "")} style={{ marginLeft: "auto" }}
              onClick={() => { const nv = !own; setOwn(nv); const cur = lines(sel); if (cur.length) setSel([...new Set(nv ? cur.map((e) => OWN.test(e) ? e : `own && ${e}`) : cur.map((e) => e.replace(/^\s*own\s*&&\s*/, "")))].join("\n")); }}>Own Only</span>
            <a href="#" onClick={(e) => { e.preventDefault(); setView(view === "vars" ? "res" : "vars"); }} style={{ fontSize: 12 }}>Attributes?</a>
          </div>
          <div className="panel-scroll">
            {view === "vars" ?
              <div className="mut" style={{ fontSize: 12 }}>
                <b>Usable attributes:</b> {(Array.isArray(vocab.selection_variables) ? vocab.selection_variables : []).join(", ")}.<br /><br />
                <b>Rejected by the platform:</b> {(Array.isArray(vocab.selection_non_variables) ? vocab.selection_non_variables : []).join(", ")} — there is no way to filter on performance.
                <div style={{ marginTop: 8 }}><span className="pill" onClick={() => setView("res")}>← Back</span></div>
              </div> :
              <>
                <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8 }}>
                  <label className="fld"><span>Template ({"{name}"} Placeholders)</span><input value={tpl} onChange={(e) => setTpl(e.target.value)} placeholder='in(datacategories,"{cat}") && turnover<{tr}' /></label>
                </div>
                <label className="fld" style={{ marginTop: 6 }}><span>Variables (name = a, b, c Per Line)</span>
                  <textarea value={vars} onChange={(e) => setVars(e.target.value)} style={{ minHeight: 40 }} placeholder="cat = news, analyst&#10;tr = 0.2, 0.3" /></label>
                <div className="dx-filters" style={{ marginTop: 6 }}>
                  <button className="btn ghost sm" onClick={expand} disabled={busyExp}>{busyExp ? <><span className="spin" /> Expanding…</> : "Expand"}</button>
                  <button className="btn ghost sm" onClick={() => suggest("selection")} disabled={busySugS}>{busySugS ? <><span className="spin" /> Suggesting…</> : "✦ Suggest"}</button>
                </div>
                <label className="fld" style={{ marginTop: 6 }}><span>Selection Expressions ({lines(sel).length})</span>
                  <textarea value={sel} onChange={(e) => setSel(e.target.value)} style={{ minHeight: 80 }} /></label>
                <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                  <button className="btn" style={{ flex: 1 }} onClick={count} disabled={cntBusy}>{cntBusy ? <><span className="spin" /> Counting…</> : "Count Selections (Need ≥10)"}</button>
                </div>
                {cntOut ?
                  <div style={{ marginTop: 8 }}>
                    <div className="dx-filters"><span className="badge ok">{cntOut.usable} usable</span><span className="badge bad">{cntOut.checked - cntOut.usable} too few</span>
                      {cntOut.usable ? <button className="btn ghost sm" onClick={keepUsable}>Keep Usable</button> : null}</div>
                    <table style={{ marginTop: 6 }}><tbody>{(cntOut.results || []).slice(0, 12).map((r: any, i: number) => (
                      <tr key={i}><td><b style={{ color: `var(--${r.usable ? "ok" : "bad"})` }}>{r.count ?? "—"}</b></td><td><code style={{ fontSize: 11 }}>{r.selection.slice(0, 46)}</code></td></tr>))}</tbody></table>
                  </div> : null}
              </>}
          </div>
        </div>

        {/* combos + run + results */}
        <div className="panel dx-panel">
          <div className="dx-head"><b>2 · Combo & Run</b><span className="mut">How they are weighted</span></div>
          <div className="panel-scroll">
            <label className="fld"><span>Combo Expressions ({lines(combo).length}, 1 = Equal Weight)</span>
              <textarea value={combo} onChange={(e) => setCombo(e.target.value)} style={{ minHeight: 44 }} /></label>
            <div className="dx-filters" style={{ marginTop: 4 }}>
              <button className="btn ghost sm" onClick={() => suggest("combo")} disabled={busySugC}>{busySugC ? <><span className="spin" /> Suggesting…</> : "✦ Suggest Combos"}</button>
              <button className="btn ghost sm" onClick={checkCombos} disabled={comboChkBusy}>{comboChkBusy ? <span className="spin" /> : "✓ Check Combos"}</button>
            </div>
            {comboChk ? <div style={{ fontSize: 12, marginTop: 4 }}>
              {comboChk.map((c, i) => <div key={i} style={{ color: c.ok ? "var(--ok)" : "var(--bad)" }}>{c.ok ? "✓" : "✗"} <code style={{ fontSize: 11 }}>{c.expr.slice(0, 40)}</code>{c.ok ? "" : " — " + c.issues.map((x: any) => x.message || x.code).join(", ")}</div>)}
            </div> : null}

            <label style={{ fontSize: 11, color: "var(--mut)", marginTop: 8, display: "block" }}>Universes</label>
            <div className="dx-filters wrap">{universes.map((u) => <span key={u} className={"pill" + (unis.includes(u) ? " on" : "")} onClick={() => toggle(unis, u, setUnis)}>{u}</span>)}</div>
            <label style={{ fontSize: 11, color: "var(--mut)" }}>Neutralizations</label>
            <div className="dx-filters wrap">{neutOpts.map((nn) => <span key={nn} className={"pill" + (neuts.includes(nn) ? " on" : "")} onClick={() => toggle(neuts, nn, setNeuts)}>{nn}</span>)}</div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8, marginTop: 8 }}>
              <label className="fld"><span>|Sharpe| ≥ {sDelay === 0 ? "(D0)" : ""}</span><NumberInput step="0.01" min={0} fallback={0} value={gate.sharpe} onChange={(v) => setGate({ ...gate, sharpe: v })} /></label>
              <label className="fld"><span>|Fitness| ≥</span><NumberInput step="0.1" min={0} fallback={0} value={gate.fitness} onChange={(v) => setGate({ ...gate, fitness: v })} /></label>
              <label className="fld"><span>Concurrency (≤3)</span><NumberInput min={1} max={3} fallback={3} value={conc} onChange={setConc} /></label>
              <label className="fld"><span>Tag</span><input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="my_super" /></label>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 8, marginTop: 8 }}>
              <label className="fld"><span>Winner Tag</span><input value={winnerTag} onChange={(e) => setWinnerTag(e.target.value)} placeholder="my_super_winner" /></label>
            </div>

            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button className="btn" style={{ flex: 1 }} onClick={run} disabled={busy}>{busy ? <><span className="spin" /> Simulating…</> : "▶ Run SuperAlpha Simulation"}</button>
              {busy ? <button className="btn ghost" onClick={stop}>Stop</button> : null}
            </div>
            {prog ? <div className="mut" style={{ fontSize: 12, marginTop: 6 }}><span className="spin" /> {prog}</div> : null}

            {res ?
              <div style={{ marginTop: 10 }}>
                <div className="dx-filters"><span className="badge ok">{res.passed} passed</span><span className="mut">of {res.simulated} · {res.failed || 0} failed</span></div>
                {(res.errors || []).length ? <div className="mut" style={{ fontSize: 11, color: "var(--bad)", margin: "4px 0" }}>{res.errors.slice(0, 2).join("; ")}</div> : null}
                <table style={{ marginTop: 6 }}><thead><tr><th></th><th>Alpha</th><th>Sharpe</th><th>Fit</th><th>Why Not</th></tr></thead>
                  <tbody>{(res.results || []).map((r: any, i: number) => (
                    <tr key={i}><td>{r.passed ? <span style={{ color: "var(--ok)" }}>✓</span> : <span style={{ color: "var(--bad)" }}>✗</span>}</td>
                      <td>{r.alpha_id ? <a href={`https://platform.worldquantbrain.com/alpha/${r.alpha_id}`} target="_blank" rel="noopener"><code>{r.alpha_id}</code></a> : "—"}
                        <div className="mut" style={{ fontSize: 10 }}>{r.universe} · {r.neutralization}</div></td>
                      <td>{num(r.sharpe)}</td><td>{num(r.fitness)}</td><td className="mut" style={{ fontSize: 11 }}>{(r.reasons || []).slice(0, 1).join(", ")}</td></tr>))}</tbody></table>
              </div> : null}
          </div>
        </div>
      </div>
    </div>
  );
}
