import { useEffect, useRef, useState } from "react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";

interface Corr {
  alpha_ids: string[]; threshold: number; days: number;
  matrix: (number | null)[][]; correlated_pairs: { a: string; b: string; correlation: number }[];
  clusters: string[][]; best_set: string[]; missing: string[];
}
interface ProdRow { alpha_id: string; value: number | null; result: string; submittable: boolean; error?: string; }
interface Prod { threshold: number; results: ProdRow[]; submittable: string[]; n_submittable: number; }

// Correlation & Portfolio.
//   • PRODUCTION correlation is the real submission gate — checked PER ALPHA. An alpha is
//     submittable only when its prod-corr is below 0.70. BRAIN accepts one submission at a
//     time, so this is never a "submit these N together" verdict.
//   • The pairwise matrix is a DIVERSIFICATION aid — how your own candidates move relative to
//     each other — not the submission gate.
// The studio never submits; you submit on BRAIN yourself.
export function Portfolio() {
  const { toast, toastErr } = useToast();
  const [ids, setIds] = useState("");
  const [threshold, setThreshold] = useState(0.7);
  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState("");
  const [c, setC] = useState<Corr | null>(null);
  const [pc, setPc] = useState<Prod | null>(null);
  const [pcBusy, setPcBusy] = useState(false);
  const jobRef = useRef("");

  useEffect(() => {
    api.get<{ alpha_ids: string[] }>("/analytics/passed").then((d) => {
      if (d.alpha_ids?.length) setIds(d.alpha_ids.join("\n"));
    });
  }, []);

  const list = ids.split("\n").map((s) => s.trim()).filter(Boolean);

  // The submission gate: production correlation, per alpha.
  async function checkProd() {
    if (!list.length) return toast("Add at least one alpha id.", "warn");
    setPcBusy(true); setPc(null);
    const start = await api.post<any>("/analytics/prodcorr", { alpha_ids: list, threshold });
    if (start.error || !start.job_id) { setPcBusy(false); return toastErr(start.error || "could not start"); }
    let s: any = {};
    for (; ;) { s = await api.get(`/analytics/jobs/${start.job_id}`); if (s.message) setProg(s.message); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1500)); }
    setPcBusy(false); setProg("");
    if (s.status !== "done") return toastErr(s.error || s.status);
    setPc(s.result);
    toast(`${s.result?.n_submittable ?? 0}/${list.length} submittable (prod-corr < ${threshold}).`, "ok");
  }

  // Diversification view: pairwise PnL correlation between YOUR candidates.
  async function run() {
    if (list.length < 2) return toast("Give at least two alpha ids for the diversification view.", "warn");
    setBusy(true); setC(null); setProg("starting…");
    const start = await api.post<{ job_id: string; error?: string }>("/analytics/correlation", { alpha_ids: list, threshold });
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "could not start"); }
    jobRef.current = start.job_id;
    let s: any = {};
    for (;;) { s = await api.get(`/analytics/jobs/${start.job_id}`); if (s.message) setProg(s.message); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setBusy(false); setProg("");
    if (s.status !== "done") return toastErr(s.error || s.status);
    setC(s.result); toast(`Diversification: largest mutually-uncorrelated group is ${s.result?.best_set?.length ?? 0}.`, "ok");
  }

  const cell = (v: number | null) => v == null ? "—" : v.toFixed(2);
  const bg = (v: number | null) => v != null && c && v >= c.threshold ? "#cf4b4b26" : "transparent";

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      <div className="panel" style={{ padding: 14, maxWidth: 320 }}>
        <div className="dx-head"><b>Alphas</b><span className="mut">{list.length}</span></div>
        <div className="mut" style={{ fontSize: 12, marginBottom: 6 }}>Metric-passing alphas are prefilled. One id per line.</div>
        <textarea value={ids} onChange={(e) => setIds(e.target.value)} style={{ flex: 1, minHeight: 200 }} className="panel-scroll" />
        <label className="fld" style={{ marginTop: 8 }}><span>Correlation threshold</span>
          <input type="number" step="0.05" value={threshold} onChange={(e) => setThreshold(+e.target.value || 0.7)} /></label>
        <button className="btn" style={{ marginTop: 8 }} onClick={checkProd} disabled={pcBusy}>
          {pcBusy ? <><span className="spin" /> {prog || "prod-corr…"}</> : "① Check prod-corr (submission gate)"}</button>
        <button className="btn ghost" style={{ marginTop: 6 }} onClick={run} disabled={busy}>
          {busy ? <><span className="spin" /> {prog}</> : "② Diversification view"}</button>
      </div>

      <div className="panel" style={{ padding: 14 }}>
        <div className="panel-scroll">
          {/* ── Submission gate: production correlation, per alpha ───────────────────── */}
          <div className="dx-head"><b>Submission gate · production correlation</b>
            {pc ? <span className="mut">{pc.n_submittable}/{pc.results.length} submittable</span> : null}</div>
          {!pc ? <div className="empty">Run “Check prod-corr” to see which alphas are submittable. An alpha is submittable only when its production correlation is below {threshold}.</div> :
            <>
              <div className="mut" style={{ fontSize: 11, marginBottom: 8 }}>
                BRAIN accepts <b>one submission at a time</b>. Each alpha is judged on <b>its own</b> production
                correlation — this is not a “submit together” set. Submit any ✓ alpha on BRAIN yourself.
              </div>
              <table><thead><tr><th></th><th>alpha</th><th>prod-corr</th><th>status</th></tr></thead>
                <tbody>{pc.results.map((r) => (
                  <tr key={r.alpha_id}>
                    <td>{r.submittable ? <span style={{ color: "var(--ok)" }}>✓</span> : <span style={{ color: "var(--bad)" }}>✗</span>}</td>
                    <td><a href={`https://platform.worldquantbrain.com/alpha/${r.alpha_id}`} target="_blank" rel="noopener"><code>{r.alpha_id}</code></a></td>
                    <td style={{ color: r.value != null && r.value >= threshold ? "var(--bad)" : r.value != null ? "var(--ok)" : undefined }}>
                      {r.value == null ? "—" : r.value.toFixed(3)}</td>
                    <td className="mut" style={{ fontSize: 11 }}>
                      {r.submittable ? "submittable" : r.result === "NONE" ? "no prod-corr yet" : r.error ? r.error : "correlated ≥ threshold"}</td>
                  </tr>))}</tbody></table>
            </>}

          {/* ── Diversification: mutual correlation between your candidates ──────────── */}
          <div className="dx-head" style={{ marginTop: 16 }}><b>Diversification · candidate correlation</b>
            {c ? <span className="mut">{c.alpha_ids.length} alphas · {c.days} days</span> : null}</div>
          {!c ? <div className="empty">Run the diversification view to see how your candidates correlate with each other. This is guidance for spreading risk — not the submission gate.</div> :
            <>
              <div className="panel" style={{ padding: "10px 12px", boxShadow: "none", background: "var(--acc-weak)", borderColor: "var(--acc)", marginBottom: 10 }}>
                <b style={{ color: "var(--acc)" }}>Largest mutually-uncorrelated group: {c.best_set.length}</b>
                {c.best_set.length ? <div className="mut" style={{ fontSize: 12, marginTop: 3 }}>{c.best_set.join(", ")}</div> : null}
                <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>These move independently of each other (pairwise corr &lt; {c.threshold}) — a well-diversified set to submit over time. Submission is still one alpha at a time, each gated by its own prod-corr above.</div>
              </div>
              {c.alpha_ids.length <= 14 ?
                <table><thead><tr><th></th>{c.alpha_ids.map((id) => <th key={id}>{id.slice(0, 5)}</th>)}</tr></thead>
                  <tbody>{c.matrix.map((row, i) => (
                    <tr key={i}><th>{c.alpha_ids[i].slice(0, 5)}</th>
                      {row.map((v, j) => <td key={j} style={{ background: bg(v), textAlign: "center" }}>{cell(v)}</td>)}</tr>))}</tbody></table>
                : <div className="mut" style={{ fontSize: 12 }}>Matrix hidden ({c.alpha_ids.length} alphas — too wide). The group above is the takeaway.</div>}
              {c.missing.length ? <div className="mut" style={{ fontSize: 11, marginTop: 8 }}>No PnL for: {c.missing.join(", ")}</div> : null}
            </>}
        </div>
      </div>
    </div>
  );
}
