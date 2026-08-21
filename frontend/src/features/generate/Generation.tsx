import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";

// Generation v2: deep single-field extraction or ≤2-category multi-field, both LLM-driven,
// validated/repaired server-side, and steered by the personal diversity engine. Setup and
// results persist across navigation and reloads; a running job reconnects.
export function Generation() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();
  const [mode, setMode] = usePersistentState<"single" | "multi">("gen:mode", "single");
  const [source, setSource] = usePersistentState<"fields" | "bulk">("gen:source", "fields");
  const [prompt, setPrompt] = usePersistentState("gen:prompt", "");
  const [bulkText, setBulkText] = usePersistentState("gen:bulktext", "");
  const [unmatched, setUnmatched] = usePersistentState<string[]>("gen:unmatched", []);
  const [maxOps, setMaxOps] = usePersistentState("gen:maxops", 4);
  const [n, setN] = usePersistentState("gen:n", 12);
  const [valid, setValid] = usePersistentState<string[]>("gen:valid", []);
  const [rejected, setRejected] = usePersistentState<{ expr: string; issues: string[] }[]>("gen:rejected", []);
  const [meta, setMeta] = usePersistentState<{ provider?: string; used?: string[]; fields?: string[] }>("gen:meta", {});
  const [seen, setSeen] = usePersistentState<string[]>("gen:seen", []);   // dedup across runs
  const [jobId, setJobId] = usePersistentState<string>("gen:job", "");
  const [jobKind, setJobKind] = usePersistentState<"run" | "bulk">("gen:jobkind", "run");
  const [busy, setBusy] = useState(false);
  const [rewriteBusy, setRewriteBusy] = useState(false);
  const [libPrompts, setLibPrompts] = useState<{ id: number; name: string; body: string; datasets?: string[] }[]>([]);
  const [reqDatasets, setReqDatasets] = usePersistentState<string[]>("gen:reqds", []);

  useEffect(() => { api.get<any>("/generate/prompts?scope=generate").then((d) => setLibPrompts(d.prompts || [])); }, []);
  // Reconnect a running generation job on return / reload.
  useEffect(() => { if (jobId) { setBusy(true); poll(jobId, jobKind); } /* eslint-disable-next-line */ }, []);

  async function poll(id: string, kind: "run" | "bulk" = "run") {
    let s: any = {};
    for (; ;) { s = await api.get(`/generate/jobs/${id}`); if (s.error && s.status === undefined) break; if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setBusy(false); setJobId("");
    if (s.status !== "done") { if (s.error) toastErr(s.error); return; }
    const r = s.result || {};
    // Dedup against everything generated before — only keep genuinely new expressions.
    const all: string[] = r.valid || [];
    const fresh = all.filter((e) => !seen.includes(e));
    const dups = all.length - fresh.length;
    setValid(fresh); setRejected(r.rejected || []);
    setSeen([...new Set([...seen, ...all])].slice(-2000));
    if (kind === "bulk") {
      setUnmatched(r.unmatched || []);
      setMeta({ fields: r.matched_field_ids });
      toast(`${fresh.length} new (${r.single?.length || 0} single-field, ${r.multi?.length || 0} combination)`
        + (dups ? ` · ${dups} duplicate(s) skipped` : "")
        + (r.unmatched?.length ? ` · ${r.unmatched.length} pasted line(s) unmatched` : ""));
    } else {
      setUnmatched([]);
      setMeta({ provider: r.provider, used: r.operators_used, fields: r.fields_used });
      toast(`${fresh.length} new via ${r.provider}${dups ? ` · ${dups} duplicate(s) skipped` : ""} · ${r.operators_used?.length || 0} operators.`);
    }
  }

  const selFields = R.fields.filter((f) => R.selFields.includes(f.id));
  const dsById = Object.fromEntries(R.datasets.map((d) => [d.id, d]));
  const fieldCategory: Record<string, string> = {};
  selFields.forEach((f) => { const c = f.dataset_id && dsById[f.dataset_id]?.category_id; if (c) fieldCategory[f.id] = c; });
  const dsNames = R.datasets.filter((d) => R.selDatasets.includes(d.id)).map((d) => d.name || d.id);
  const nCats = new Set(Object.values(fieldCategory)).size;

  // Which datasets (by id) a saved strategy prompt needs but that aren't fetched yet.
  const fetchedIds = R.datasets.map((d) => d.id.toLowerCase());
  const missingDs = reqDatasets.filter((d) => !fetchedIds.includes(d.toLowerCase()));

  const fieldPayload = () => selFields.map((f) => ({ id: f.id, type: f.type, description: f.description, dataset_id: f.dataset_id }));

  async function autoRewrite() {
    if (!selFields.length && !prompt.trim()) return toast("Select datafields, or describe the idea so the LLM can pick the data itself.", "warn");
    setRewriteBusy(true);
    const start = await api.post<any>("/generate/rewrite", {
      prompt, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument, universe: R.ctx.universe,
      dataset_names: dsNames, fields: fieldPayload(), categories: fieldCategory, max_operators: maxOps, n,
    });
    if (start.error || !start.job_id) { setRewriteBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/generate/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setRewriteBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    if (s.result?.prompt) { setPrompt(s.result.prompt); toast("Rewrote into a rich instruction — the datasets & operators are added automatically at generation.", "ok"); }
  }

  async function run() {
    if (source === "bulk") {
      if (!bulkText.trim()) return toast("Paste the datafield descriptions to build from — one per line.", "warn");
      setBusy(true); setValid([]); setRejected([]); setUnmatched([]);
      const start = await api.post<{ job_id: string; error?: string }>("/generate/bulk", {
        text: bulkText, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
        universe: R.ctx.universe, max_operators: maxOps, n, rounds: 4,
      });
      if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
      setJobId(start.job_id); setJobKind("bulk"); poll(start.job_id, "bulk");
      return;
    }
    if (!selFields.length && !prompt.trim()) return toast("Select datafields, or describe the idea you want tested so the LLM can pick the data itself.", "warn");
    if (missingDs.length) return toast(`Fetch the required dataset(s) first: ${missingDs.join(", ")}.`, "warn");
    setBusy(true); setValid([]); setRejected([]); setUnmatched([]);
    const start = await api.post<{ job_id: string; error?: string }>("/generate/run", {
      mode, prompt, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument,
      universe: R.ctx.universe, dataset_names: dsNames, fields: fieldPayload(),
      categories: fieldCategory, max_operators: maxOps, n,
    });
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
    setJobId(start.job_id); setJobKind("run"); poll(start.job_id, "run");
  }

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      {/* setup */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>Generation</b>
          <span className="mut">{selFields.length} field(s) · {nCats} categor{nCats === 1 ? "y" : "ies"} · {R.ctx.region} D{R.ctx.delay}</span></div>

        <div className="dx-filters" style={{ marginBottom: 8 }}>
          <span className={"pill" + (source === "fields" ? " on" : "")} onClick={() => setSource("fields")}>Selected Fields / Idea</span>
          <span className={"pill" + (source === "bulk" ? " on" : "")} onClick={() => setSource("bulk")}>Paste Datafield Descriptions</span>
        </div>

        {source === "fields" ? <>
          <div className="dx-filters" style={{ marginBottom: 8 }}>
            <span className={"pill" + (mode === "single" ? " on" : "")} onClick={() => setMode("single")}>Single Field (Deep Extraction)</span>
            <span className={"pill" + (mode === "multi" ? " on" : "")} onClick={() => setMode("multi")}>Multi Field (≤2 Categories)</span>
          </div>
          {mode === "multi" && nCats > 2 &&
            <div className="mut" style={{ fontSize: 12, color: "var(--warn)", marginBottom: 8 }}>
              {nCats} categories selected — expressions will be restricted to at most 2 per expression.</div>}

          <label className="fld"><span>Instruction (Optional — Research Lab Pushes Land Here)</span>
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} style={{ minHeight: 90 }}
              placeholder={selFields.length ? "Describe the economic ideas to explore, or leave blank for grounded auto-generation." : "No fields selected — describe the idea here and the LLM will pick the catalogued data itself."} /></label>

          {libPrompts.length > 0 &&
            <div className="dx-filters wrap" style={{ marginTop: 8 }}>
              <span className="mut" style={{ fontSize: 11 }}>From Library:</span>
              {libPrompts.slice(0, 8).map((p) => <span key={p.id} className="pill" title={p.name}
                onClick={() => { setPrompt(p.body); setReqDatasets(p.datasets || []); toast(`Loaded “${p.name}” (#${p.id}).`); }}>
                {p.name.length > 22 ? p.name.slice(0, 22) + "…" : p.name} <span className="mut">#{p.id}</span></span>)}
            </div>}
          {missingDs.length ? <div className="mut" style={{ fontSize: 12, color: "var(--warn)", marginTop: 8 }}>
            ⚠ This strategy needs data you haven't fetched: <b>{missingDs.join(", ")}</b>. Fetch it in the Data Explorer and select its fields before generating.
          </div> : null}

          <div className="dx-filters" style={{ marginTop: 8 }}>
            <button className="btn ghost sm" onClick={autoRewrite} disabled={rewriteBusy || busy}>
              {rewriteBusy ? <><span className="spin" /> Rewriting…</> : "✦ Auto-Rewrite Instruction"}</button>
            <span className="mut" style={{ fontSize: 11 }}>datasets & operators are added automatically</span>
          </div>
        </> : <>
          <label className="fld"><span>Datafield Descriptions — One Per Line</span>
            <textarea value={bulkText} onChange={(e) => setBulkText(e.target.value)} style={{ minHeight: 180 }}
              placeholder={"close\nfnd28_newqtr: quarterly earnings surprise magnitude\nvolume traded over the last 20 days\n…paste as many as you have, one per line"} /></label>
          <div className="mut" style={{ fontSize: 11, marginTop: 6 }}>
            Matched against the local catalogue for {R.ctx.region} D{R.ctx.delay} {R.ctx.universe} — a bare field id matches most reliably;
            an id with a description, or just a description, also works. Generates single-field alphas for every match individually,
            AND ≤2-category combinations across them — as many distinct, valid expressions as the catalogue supports.</div>
          {unmatched.length > 0 && <div className="mut" style={{ fontSize: 11, marginTop: 6, color: "var(--warn)" }}>
            ⚠ {unmatched.length} pasted line(s) didn't match anything: {unmatched.slice(0, 5).join(" · ")}{unmatched.length > 5 ? "…" : ""}</div>}
        </>}

        <div style={{ display: "flex", gap: 8, alignItems: "end", marginTop: 10 }}>
          <label className="fld" style={{ width: 84 }}><span>Max Ops</span>
            <NumberInput min={1} max={12} fallback={4} value={maxOps} onChange={setMaxOps} /></label>
          <label className="fld" style={{ width: 84 }}><span>Count</span>
            <NumberInput min={1} fallback={12} value={n} onChange={setN} /></label>
          <button className="btn" onClick={run} disabled={busy} style={{ flex: 1 }}>
            {busy ? <><span className="spin" /> generating…</> : "✦ Generate"}</button>
        </div>
        {meta.used?.length ? <div className="mut" style={{ fontSize: 11, marginTop: 8 }}>operators this batch: {meta.used.join(", ")}</div> : null}
      </div>

      {/* results */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>Expressions</b>
          {valid.length ? <span className="mut">{valid.length} valid · {rejected.length} rejected</span> : null}
          {valid.length ? <button className="btn sm" style={{ marginLeft: "auto" }}
            onClick={() => { R.setPendingExperimentId(null); R.setPending(valid); nav("/simulate"); }}>Send {valid.length} To Simulate →</button> : null}
          {valid.length ? <button className="btn ghost sm"
            onClick={() => { navigator.clipboard?.writeText(valid.join("\n")); toast("Copied."); }}>Copy</button> : null}
        </div>
        {(meta.used?.length || meta.fields?.length) ? (
          <div className="dx-filters wrap" style={{ marginBottom: 6, fontSize: 11 }}>
            {meta.used ? <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>{meta.used.length} operators</span> : null}
            <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>{meta.fields?.length || 0} fields</span>
            <span className="badge" style={{ background: "var(--faint)", color: "var(--mut)" }}>{seen.length} seen total</span>
            {meta.used ? <span className="mut" title={meta.used.join(", ")}>{meta.used.slice(0, 10).join(", ")}{meta.used.length > 10 ? "…" : ""}</span> : null}
          </div>
        ) : null}
        <div className="panel-scroll">
          {!valid.length && !rejected.length ? <div className="empty">Pick fields and Generate, or leave fields empty and describe an idea in the instruction box — the LLM will pick the catalogued data itself. Single-field extracts signal from one field every way; multi-field combines up to two categories.</div> :
            <>
              {valid.map((e, i) => <code key={i} style={{ display: "block", padding: "6px 8px", borderBottom: "1px solid var(--line)" }}>{e}</code>)}
              {rejected.length > 0 && <>
                <div className="dx-head" style={{ marginTop: 10 }}><span className="mut">Rejected ({rejected.length})</span></div>
                {rejected.slice(0, 8).map((r, i) => (
                  <div key={i} style={{ padding: "4px 8px", fontSize: 12 }}>
                    <code className="mut">{r.expr.slice(0, 60)}</code> <span style={{ color: "var(--bad)" }}>{r.issues.join(", ")}</span></div>))}
              </>}
            </>}
        </div>
      </div>
    </div>
  );
}
