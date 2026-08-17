import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";
import "../templates/templates.css";

interface Hyp {
  idea: string; mechanism: string; sign: string; horizon: string; expression: string;
  confidence?: number; expression_valid?: boolean; expression_issues?: string[];
}

const MODES: { id: string; label: string; hint: string }[] = [
  { id: "single", label: "Single Field", hint: "One datafield per idea (default)" },
  { id: "multi_single_dataset", label: "Multi-Field · One Dataset", hint: "Combine fields from the same dataset" },
  { id: "multi_two_categories", label: "Multi-Field · Two Categories", hint: "Combine fields from at most two categories" },
];

// The Research Lab: LLM research grounded in the selected datasets/fields and region,
// optionally seeded by a research paper. It produces structured, validated hypotheses that
// the user then PUSHES to generation — research and building are deliberately separate steps.
// All setup and results persist across navigation and reloads; a running job reconnects.
export function ResearchLab() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();
  const [providers, setProviders] = useState<{ available: string[]; used: string[] }>({ available: [], used: [] });

  const [goal, setGoal] = usePersistentState("research:goal", "");
  const [n, setN] = usePersistentState("research:n", 6);
  const [mode, setMode] = usePersistentState("research:mode", "single");
  const [maxOps, setMaxOps] = usePersistentState("research:maxops", 6);
  const [paperText, setPaperText] = usePersistentState("research:paperText", "");
  const [paperName, setPaperName] = usePersistentState("research:paperName", "");
  const [pages, setPages] = usePersistentState("research:pages", "");
  const [community, setCommunity] = usePersistentState("research:community", false);
  const [hyps, setHyps] = usePersistentState<Hyp[]>("research:hyps", []);
  const [meta, setMeta] = usePersistentState<{ provider?: string; session_id?: number }>("research:meta", {});
  const [jobId, setJobId] = usePersistentState<string>("research:job", "");

  const [busy, setBusy] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [autoBusy, setAutoBusy] = useState(false);
  const [sortConf, setSortConf] = usePersistentState("research:sortconf", false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => { api.get<any>("/research/providers").then(setProviders); }, []);

  const selFields = R.fields.filter((f) => R.selFields.includes(f.id));
  const dsById = Object.fromEntries(R.datasets.map((d) => [d.id, d]));
  const dsNames = R.datasets.filter((d) => R.selDatasets.includes(d.id)).map((d) => d.name || d.id);
  const categories: Record<string, string> = {};
  selFields.forEach((f) => { const c = f.dataset_id && dsById[f.dataset_id]?.category_id; if (c) categories[f.id] = c; });
  const category = [...new Set(Object.values(categories))].join(", ");

  // Reconnect to a running research job (survives navigation + reload).
  useEffect(() => { if (jobId) { setBusy(true); poll(jobId); } /* eslint-disable-next-line */ }, []);

  async function uploadPaper(file: File) {
    if (community && !pages.trim())
      return toast("For a WorldQuant community paper, enter the page range that describes the datasets/fields.", "warn");
    const fd = new FormData(); fd.append("file", file); fd.append("pages", pages);
    toast("Reading PDF…");
    const d = await api.upload<{ text: string; chars: number; name: string }>("/research/paper", fd);
    if (d.error) return toastErr(d.error);
    setPaperText(d.text); setPaperName(d.name);
    toast(`Loaded ${d.chars.toLocaleString()} chars from ${d.name}.`);
  }

  async function poll(id: string) {
    let s: any = {};
    for (; ;) {
      s = await api.get(`/research/jobs/${id}`);
      if (s.error && s.status === undefined) break;
      if (s.status !== "running") break;
      await new Promise((r) => setTimeout(r, 1400));
    }
    setBusy(false); setJobId("");
    if (s.status !== "done") { if (s.error) toastErr(s.error); return; }
    const r = s.result || {};
    setHyps(r.hypotheses || []); setMeta({ provider: r.provider, session_id: r.session_id });
    const valid = (r.expressions || []).length;
    toast(`${(r.hypotheses || []).length} hypotheses (${valid} with valid expressions) via ${r.provider}.`);
  }

  async function run() {
    if (!providers.available.length) return toast("No AI provider set up. Add a key in Settings.", "warn");
    if (!selFields.length) return toast("Select datafields in the Data Explorer first.", "warn");
    if (community && paperText && !pages.trim())
      return toast("A community paper needs its page range specified.", "warn");
    setBusy(true); setHyps([]);
    const start = await api.post<{ job_id: string; error?: string }>("/research/run", {
      category, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
      dataset_names: dsNames, fields: selFields.map((f) => ({ id: f.id, type: f.type, description: f.description })),
      categories, goal, paper_text: paperText, paper_name: paperName, paper_is_community: community,
      mode, max_operators: maxOps, n,
    });
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
    setJobId(start.job_id);
    poll(start.job_id);
  }

  async function runAutopilot() {
    if (!providers.available.length) return toast("No AI provider set up. Add a key in Settings.", "warn");
    if (community && paperText && !pages.trim())
      return toast("A community paper needs its page range specified.", "warn");
    setAutoBusy(true); setHyps([]);
    const start = await api.post<{ job_id: string; error?: string }>("/research/autopilot", {
      region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
      category, goal, paper_text: paperText, paper_name: paperName,
      n, max_operators: maxOps, simulate: true,
      universes: ["TOP3000"], neutralizations: ["INDUSTRY"],
    });
    if (start.error || !start.job_id) {
      setAutoBusy(false);
      return toastErr(start.error || "Could not start autonomous research.");
    }
    let s: any = {};
    for (;;) {
      s = await api.get(`/research/jobs/${start.job_id}`);
      if (s.status !== "running") break;
      await new Promise((r) => setTimeout(r, 1400));
    }
    setAutoBusy(false);
    if (s.status !== "done") return toastErr(s.error || "Autonomous research failed.");
    const r = s.result || {};
    setHyps(r.hypotheses || []);
    setMeta({ provider: r.provider, session_id: r.session_id });
    const valid = (r.expressions || []).length;
    const sim = r.simulation;
    toast(
      `Autopilot: ${r.hypotheses?.length || 0} hypotheses · ${valid} valid expressions · ` +
      `${r.datasets_scanned || 0} datasets scanned · ${r.fields_matched || 0} fields matched` +
      (sim ? ` · ${sim.passed || 0} passed simulation` : ""),
      "ok"
    );
  }

  async function pushToGenerate() {
    const usable = hyps.filter((h) => h.expression && h.expression_valid !== false);
    if (!usable.length) return toast("No hypothesis has a valid expression to push yet.", "warn");
    const body =
      "Build alphas that TEST each of the researched hypotheses below. Focus on the HYPOTHESIS " +
      "(the economic mechanism) — the example expression only ILLUSTRATES the idea and is NOT a field " +
      "restriction. Use whichever of the selected datafields best express each hypothesis, drawing on " +
      "ALL of them across the batch where they fit.\n\n" +
      usable.map((h, i) => `${i + 1}. ${h.idea} — ${h.mechanism} (sign ${h.sign}, ~${h.horizon}d). Illustrative form: ${h.expression}`).join("\n");
    // No "fetch these first" datasets: research is already grounded in the fields you selected,
    // so the Generation warning (which is for Strategy Atlas prompts) must not fire here.
    setPushBusy(true);
    const start = await api.post<any>("/research/push", {
      scope: "generate", category, region: R.ctx.region, body, dataset_names: [],
      research_id: meta.session_id || 0, compose: true, source: "research",
    });
    if (start.error || !start.job_id) { setPushBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/research/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setPushBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    const r = s.result || {};
    // Research is already grounded in the selected fields — load it straight into Generation.
    localStorage.setItem("ace2:gen:prompt", JSON.stringify(body));
    localStorage.setItem("ace2:gen:reqds", JSON.stringify([]));
    toast(r.duplicate ? `Already saved as “${r.name}”. Opening Generation…`
      : `Saved as “${r.name}” (#${r.prompt_id}). Opening Generation…`, "ok");
    nav("/generate");
  }

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      {/* setup */}
      <div className="panel tpl-panel">
        <div className="dx-head"><b>Research Setup</b>
          <span className="mut">{providers.available.length ? `via ${providers.used.join(", ") || providers.available.join(", ")}` : "No AI key"}</span>
          {busy ? <span className="badge ok" style={{ marginLeft: "auto" }}>running</span> : null}</div>
        <div className="tpl-body">
          <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>
            {selFields.length} field(s) · {dsNames.length} dataset(s) · {R.ctx.region} D{R.ctx.delay}
            {category ? ` · ${category}` : ""}
            {selFields.length ? "" : " · Manual mode can use selected fields. Autopilot scans BRAIN and chooses fields automatically."}
          </div>

          <label className="fld"><span>Field Combination</span>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              {MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select></label>
          <div className="mut" style={{ fontSize: 11, marginTop: 3 }}>{MODES.find((m) => m.id === mode)?.hint}</div>

          <label className="fld" style={{ marginTop: 10 }}><span>Goal (Optional — More Detail Yields More Complex Hypotheses)</span>
            <textarea value={goal} onChange={(e) => setGoal(e.target.value)} style={{ minHeight: 46 }}
              placeholder="e.g. short-horizon reaction to negative news in liquid names" /></label>

          <div className="dx-head" style={{ marginTop: 12 }}><b>Research Paper</b>
            <span className="mut">Optional — extract a mechanism to adapt</span></div>
          <div className="dx-filters" style={{ marginBottom: 6 }}>
            <span className={"pill" + (community ? " on" : "")} onClick={() => setCommunity((v) => !v)}
              title="A WorldQuant community paper must be mapped to specific suggested datasets/fields and a page range.">
              WorldQuant Community Paper</span>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "end" }}>
            <label className="fld" style={{ width: 130 }}><span>Pages{community ? " (required)" : " (e.g. 1-3,5)"}</span>
              <input value={pages} onChange={(e) => setPages(e.target.value)} placeholder={community ? "required" : "whole doc"} /></label>
            <button className="btn ghost sm" onClick={() => fileRef.current?.click()}>Upload PDF</button>
            <input ref={fileRef} type="file" accept="application/pdf" style={{ display: "none" }}
              onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadPaper(f); }} />
            {paperName ? <span className="badge ok">{paperName}</span> : null}
          </div>
          <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>
            {community
              ? "Community paper: specify the datasets/fields it suggests (select them in Data Explorer) and the page range."
              : "Your own paper: proceed with any datasets. No paper? Research is as complex as your goal implies."}
          </div>
          <textarea value={paperText} onChange={(e) => setPaperText(e.target.value)} style={{ minHeight: 46, marginTop: 8 }}
            placeholder="…or paste the relevant excerpt here" />

          <div style={{ display: "flex", gap: 8, alignItems: "end", marginTop: 12 }}>
            <label className="fld" style={{ width: 92 }}><span>Hypotheses</span>
              <NumberInput min={1} fallback={6} value={n} onChange={setN} /></label>
            <label className="fld" style={{ width: 92 }}><span>Max Ops</span>
              <NumberInput min={1} max={12} fallback={6} value={maxOps} onChange={setMaxOps} /></label>
            <button className="btn" onClick={run} disabled={busy || autoBusy} style={{ flex: 1 }}>
              {busy ? <><span className="spin" /> Researching…</> : "✦ Run Research"}</button>
            <button className="btn" onClick={runAutopilot} disabled={busy || autoBusy} style={{ flex: 1 }}>
              {autoBusy ? <><span className="spin" /> Autopilot…</> : "⚡ Research → Simulate"}</button>
          </div>
        </div>
      </div>

      {/* results */}
      <div className="panel tpl-panel">
        <div className="dx-head"><b>Hypotheses</b>
          {meta.provider ? <span className="mut">via {meta.provider}</span> : null}
          {hyps.length ? <span className={"pill sm" + (sortConf ? " on" : "")} onClick={() => setSortConf((v) => !v)} title="Sort by AI confidence">★ sort</span> : null}
          {hyps.length ? <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={pushToGenerate} disabled={pushBusy}>
            {pushBusy ? <><span className="spin" /> Saving…</> : "Push To Generation →"}</button> : null}
        </div>
        <div className="tpl-body">
          {!hyps.length ? <div className="empty">Set up your research on the left and hit Run. Results are grounded in your selected fields and region, validated for field type and vector wrapping, and can be pushed to generation.</div> :
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {(sortConf ? [...hyps].sort((a, b) => (b.confidence || 0) - (a.confidence || 0)) : hyps).map((h, i) => (
                <div key={i} className="panel" style={{ padding: "10px 12px", boxShadow: "none" }}>
                  <div style={{ fontWeight: 600 }}>{i + 1}. {h.idea}</div>
                  <div className="mut" style={{ fontSize: 12, marginTop: 3 }}>{h.mechanism}</div>
                  <div className="dx-filters" style={{ marginTop: 6, marginBottom: 4 }}>
                    {h.confidence ? <span className="badge" style={{ background: "var(--ok-weak)", color: "var(--ok)" }}>{"★".repeat(h.confidence)} {h.confidence}/5</span> : null}
                    {h.sign ? <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>sign {h.sign}</span> : null}
                    {h.horizon ? <span className="badge" style={{ background: "var(--faint)", color: "var(--mut)" }}>~{h.horizon}d</span> : null}
                    {h.expression ? (h.expression_valid === false
                      ? <span className="badge bad" title={(h.expression_issues || []).join(", ")}>invalid expr</span>
                      : <span className="badge ok">valid</span>) : null}
                  </div>
                  {h.expression ? <code style={{ display: "block", background: "var(--surface-2)", padding: "6px 8px", borderRadius: 6 }}>{h.expression}</code> : null}
                </div>
              ))}
            </div>}
        </div>
      </div>
    </div>
  );
}
