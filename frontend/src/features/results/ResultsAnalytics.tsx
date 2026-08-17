import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";

const num = (v: any) => (v == null ? "—" : (+v).toFixed(3));
const pct = (v: any) => (v == null ? "—" : (v * 100).toFixed(2) + "%");
// BRAIN reports margin as a fraction (0.0012 = 12 bps). Margin is conventionally read in bps.
const bpsNum = (v: any) => (v == null ? null : v * 10000);
const bps = (v: any) => (v == null ? "—" : (v * 10000).toFixed(1) + " bps");
// A near-miss worth retrying: didn't pass, but |Sharpe| > 1 and |fitness| > 0.7.
const nearMiss = (r: any) => !r.passed && Math.abs(r.sharpe || 0) > 1 && Math.abs(r.fitness || 0) > 0.7;
const MARGIN_MIN_BPS = 5;   // "thin margin" flag: alphas earning under ~5 bps per dollar traded

interface Row {
  alpha_id: string; expr: string; region: string; delay: number; universe: string;
  sharpe: number; fitness: number; turnover: number; returns: number; margin: number; drawdown: number;
  self_corr: number; prod_corr: number;
  powerpool_corr: number; tests_failed: number; passed: boolean; reasons: string[]; tag: string;
}

// Three-state verdict. "Passed all gates" (green) requires the metric gate AND that the
// production-correlation test has actually been RUN and is below 0.70. If prod-corr was never
// run we can only say the metrics passed — never that it cleared every gate.
type Verdict = "pass" | "metrics" | "fail";
function verdict(r: Row): Verdict {
  if (!r.passed) return "fail";                                   // metric gate failed
  if (r.prod_corr == null) return "metrics";                     // prod-corr not checked yet
  if (Math.abs(r.prod_corr) >= 0.7) return "fail";               // prod-corr too high
  return "pass";                                                  // metrics + prod-corr < 0.70
}
// An alpha "needs negation" when its raw metrics are negative — the signal works inverted, so
// you'd submit -1 * it. Drawdown/margin/returns of the RAW alpha are meaningless in that case.
const needsNeg = (r: Row) => r.sharpe != null && r.sharpe < 0;

// Eval red-flags — ONLY for alphas that don't need negation (for those we recommend negating
// instead, since the raw drawdown/returns/margin would be misleading).
function evalFlags(r: Row): string[] {
  if (needsNeg(r)) return [];
  const f: string[] = [];
  if (r.drawdown != null && r.returns != null && Math.abs(r.drawdown) > Math.abs(r.returns))
    f.push("drawdown > returns");
  const mbps = bpsNum(r.margin);
  if (mbps != null && Math.abs(mbps) < MARGIN_MIN_BPS)
    f.push("thin margin");
  return f;
}
interface Insight { operator: string; count: number; avg_fitness: number; }

// Results & Analytics: filter/sort/CSV over every stored simulation result with the full
// per-metric verdict, plus the operator-fitness insights that feed generation.
export function ResultsAnalytics() {
  const { toast, toastErr } = useToast();
  const nav = useNavigate();
  const [rows, setRows] = useState<Row[]>([]);
  const [busyReuse, setBusyReuse] = useState("");
  const [sel, setSel] = useState<Row | null>(null);
  const [pnl, setPnl] = useState<{ points: { date: string; pnl: number }[]; max_drawdown: number } | null>(null);
  const [yearly, setYearly] = useState<any[]>([]);
  const [pnlBusy, setPnlBusy] = useState(false);

  async function openChart(r: Row) {
    if (!r.alpha_id) return toast("This result has no alpha id (didn't simulate on BRAIN).", "warn");
    setSel(r); setPnl(null); setYearly([]); setPnlBusy(true);
    const d = await api.get<any>(`/analytics/alpha/${r.alpha_id}/pnl`);
    setPnlBusy(false);
    if (d.error) return toastErr(d.error);
    setPnl(d.pnl || { points: [], max_drawdown: 0 }); setYearly(d.yearly || []);
  }

  // Submission-readiness (informational only — no submit action here). Prod-corr is the real
  // gate: if it hasn't been run, it's an explicit gap — never treated as "ready".
  function readiness(r: Row) {
    const gaps: string[] = [];
    if (!r.passed) gaps.push("not through the metric gate");
    if (r.prod_corr == null) gaps.push("prod-corr not checked");
    const c = (v: any, lbl: string) => { if (v != null && Math.abs(v) >= 0.7) gaps.push(`${lbl} ≥ 0.70`); };
    c(r.self_corr, "self-corr"); c(r.prod_corr, "prod-corr"); c(r.powerpool_corr, "powerpool-corr");
    return gaps;
  }

  const [prodBusy, setProdBusy] = useState(false);
  // Run BRAIN's production-correlation test (the true submission gate) for the given alphas,
  // then reload so verdicts become authoritative.
  async function runProdCorr(alphaIds: string[]) {
    const ids = [...new Set(alphaIds.filter(Boolean))];
    if (!ids.length) return toast("No metric-passing alphas with an id to check yet.", "warn");
    setProdBusy(true);
    const start = await api.post<any>("/analytics/prodcorr", { alpha_ids: ids, threshold: 0.7 });
    if (start.error || !start.job_id) { setProdBusy(false); return toastErr(start.error || "could not start"); }
    let s: any = {};
    for (; ;) { s = await api.get(`/analytics/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1500)); }
    setProdBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    await load();
    if (sel) { const fresh = (await api.get<any>("/analytics/results?limit=400")).results?.find((x: Row) => x.alpha_id === sel.alpha_id); if (fresh) setSel(fresh); }
    toast(`Prod-corr checked · ${s.result?.n_submittable ?? 0}/${ids.length} submittable (<0.70).`, "ok");
  }

  // Turn a near-miss expression back into a template ({field}/{field2}) and open it in Template
  // Studio, with the retry dataset(s) noted so the user knows which data to select.
  async function reuse(r: Row) {
    setBusyReuse(r.alpha_id || r.expr);
    const d = await api.post<any>("/generate/templatize", { expression: r.expr });
    setBusyReuse("");
    if (d.error) return toastErr(d.error);
    localStorage.setItem("ace2:tpl:text", JSON.stringify(d.template));
    localStorage.setItem("ace2:tpl:multi", JSON.stringify(!!d.multi));
    localStorage.setItem("ace2:tpl:retry", JSON.stringify(d.datasets || []));
    toast(d.datasets?.length ? `Template ready — retry with dataset(s): ${d.datasets.join(", ")}.` : "Template ready in Template Studio.", "ok");
    nav("/templates");
  }
  const [rate, setRate] = useState<{ passed: number; total: number; success_rate: number }>({ passed: 0, total: 0, success_rate: 0 });
  const [insights, setInsights] = useState<Insight[]>([]);
  const [q, setQ] = useState("");
  const [only, setOnly] = useState<"all" | "pass" | "fail">("all");
  const [sort, setSort] = useState("fit_desc");

  const [ledger, setLedger] = useState<any>(null);
  const load = async () => {
    const d = await api.get<any>("/analytics/results?limit=400");
    setRows(d.results || []); setRate({ passed: d.passed, total: d.total, success_rate: d.success_rate });
    const s = await api.get<any>("/analytics/summary");
    setInsights(s.operator_insights || []);
    const l = await api.get<any>("/analytics/ledger");
    if (!l.error) setLedger(l);
  };
  useEffect(() => { load(); }, []);

  const view = useMemo(() => {
    let r = rows.filter((x) => (only === "all" || (only === "pass" ? x.passed : !x.passed)));
    const ql = q.toLowerCase();
    if (ql) r = r.filter((x) => (x.expr || "").toLowerCase().includes(ql) || (x.alpha_id || "").toLowerCase().includes(ql));
    const f = (x: Row) => Math.abs(x.fitness || 0);
    r = [...r].sort(sort === "fit_asc" ? (a, b) => f(a) - f(b)
      : sort === "sharpe" ? (a, b) => Math.abs(b.sharpe || 0) - Math.abs(a.sharpe || 0)
        : (a, b) => f(b) - f(a));
    return r;
  }, [rows, only, q, sort]);

  function csv() {
    if (!view.length) return toast("Nothing to export.", "warn");
    const esc = (v: any) => { const s = String(v ?? ""); return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s; };
    const head = "alpha_id,passed,region,delay,universe,neutralization,sharpe,fitness,turnover,tests_failed,tag,expression";
    const body = view.map((r: any) => [r.alpha_id, r.passed, r.region, r.delay, r.universe, r.neutralization,
      r.sharpe, r.fitness, r.turnover, r.tests_failed, r.tag, r.expr].map(esc).join(","));
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([[head, ...body].join("\n")], { type: "text/csv" }));
    a.download = `ace_results_${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.appendChild(a); a.click(); a.remove();
    toast(`Exported ${view.length} row(s).`, "ok");
  }

  const maxFit = Math.max(1, ...insights.map((i) => i.avg_fitness));

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: 1, minHeight: 0 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
        {[
          { l: "Success rate", v: `${Math.round(rate.success_rate * 100)}%` },
          { l: "Passed metrics", v: rate.passed },
          { l: "Fully verified", v: rows.filter((r) => verdict(r) === "pass").length },
          { l: "Simulated", v: rate.total },
        ].map((k) => (
          <div key={k.l} className="panel" style={{ padding: 12 }}>
            <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px" }}>{k.l}</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: "var(--acc)", marginTop: 4 }}>{k.v}</div>
          </div>
        ))}
      </div>

      {ledger && ledger.total_simulated > 0 ? (
        <div className="mut" style={{ fontSize: 12, background: "var(--surface-2)", borderRadius: 8, padding: "8px 12px" }}>
          <b>Experiment ledger:</b> {ledger.total_simulated} alphas simulated
          {ledger.by_region?.length ? " · " + ledger.by_region.map((r: any) => `${r.region} ${r.simulated}`).join(" · ") : ""}.
          {ledger.total_simulated >= 200 ? <span style={{ color: "var(--warn)" }}> ⚠ Wide sweep — a single winner here may be multiple-testing overfit; confirm it walks forward across regimes/regions.</span> : null}
        </div>
      ) : null}

      <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
        <div className="panel" style={{ padding: 14 }}>
          <div className="dx-head">
            <b>Results</b>
            <input placeholder="filter expression / id" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 160 }} />
            {(["all", "pass", "fail"] as const).map((o) => <span key={o} className={"pill" + (only === o ? " on" : "")} onClick={() => setOnly(o)}>{o}</span>)}
            <select value={sort} onChange={(e) => setSort(e.target.value)} style={{ width: 120, padding: "4px 6px", fontSize: 12 }}>
              <option value="fit_desc">|fitness| ↓</option><option value="fit_asc">|fitness| ↑</option><option value="sharpe">|Sharpe| ↓</option>
            </select>
            <button className="btn ghost sm" title="Run BRAIN production-correlation for EVERY metric-passing alpha (re-checks included)"
              onClick={() => runProdCorr(rows.filter((r) => r.passed && r.alpha_id).map((r) => r.alpha_id))}
              disabled={prodBusy}>{prodBusy ? <><span className="spin" /> prod-corr…</> : "Verify prod-corr"}</button>
            <button className="btn ghost sm" onClick={csv}>CSV</button>
          </div>
          <div className="panel-scroll">
            {!view.length ? <div className="empty">No results yet — Generate → Simulate.</div> :
              <table><thead><tr><th></th><th>alpha</th><th>Sharpe</th><th>Fit</th><th>Turn</th><th>flags</th><th>why not</th><th>retry</th></tr></thead>
                <tbody>{view.map((r, i) => {
                  const vd = verdict(r);
                  return (
                  <tr key={i}>
                    <td title={vd === "pass" ? "Passed all gates (metrics + prod-corr < 0.70)"
                      : vd === "metrics" ? "Metrics passed — prod-corr NOT checked yet" : "Failed"}>
                      {vd === "pass" ? <span style={{ color: "var(--ok)" }}>✓</span>
                        : vd === "metrics" ? <span style={{ color: "var(--warn)" }}>◐</span>
                          : <span style={{ color: "var(--bad)" }}>✗</span>}</td>
                    <td>{r.alpha_id ? <a href={`https://platform.worldquantbrain.com/alpha/${r.alpha_id}`} target="_blank" rel="noopener"><code>{r.alpha_id}</code></a> : "—"}
                      <div className="mut" style={{ fontSize: 11 }}>{(r.expr || "").slice(0, 34)}</div></td>
                    <td>{num(r.sharpe)}</td><td>{num(r.fitness)}</td>
                    <td style={{ color: (r.turnover != null && r.turnover > 0.7) ? "var(--bad)" : undefined }}>{r.turnover == null ? "—" : (r.turnover * 100).toFixed(0) + "%"}</td>
                    <td style={{ fontSize: 10.5, whiteSpace: "nowrap" }}>
                      {needsNeg(r) ? <span className="badge" style={{ color: "var(--warn)" }} title="Metrics are negative — recommend submitting the negated signal (multiply by -1)">↔ negate</span>
                        : evalFlags(r).map((f) => <span key={f} className="badge bad" style={{ marginRight: 3 }} title="Eval red-flag">{f === "drawdown > returns" ? "DD>Ret" : "margin<5bps"}</span>)}</td>
                    <td className="mut" style={{ fontSize: 11 }}>{(r.reasons || []).slice(0, 2).join(", ")}</td>
                    <td style={{ whiteSpace: "nowrap" }}>
                      {r.alpha_id ? <button className="btn ghost sm" title="Equity curve & yearly stats" onClick={() => openChart(r)}>📈</button> : null}
                      {nearMiss(r) ?
                        <button className="btn ghost sm" title="Has potential — reuse as a template with the same dataset"
                          onClick={() => reuse(r)} disabled={!!busyReuse} style={{ marginLeft: 4 }}>
                          {busyReuse === (r.alpha_id || r.expr) ? <span className="spin" /> : "⟳"}</button>
                        : null}</td>
                  </tr>);})}</tbody></table>}
          </div>
        </div>

        <div className="panel" style={{ padding: 14, minWidth: 420 }}>
          {sel ? (
            <>
              <div className="dx-head"><b>{sel.alpha_id}</b>
                <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={() => setSel(null)}>← Insights</button></div>
              <div className="panel-scroll">
                {pnlBusy ? <div className="mut"><span className="spin" /> Loading equity curve…</div> :
                  !pnl?.points?.length ? <div className="empty">No PnL series available for this alpha.</div> :
                    <>
                      {(() => {
                        const pts = pnl.points, n = pts.length;
                        const ys = pts.map((p) => p.pnl), lo = Math.min(...ys), hi = Math.max(...ys), rng = hi - lo || 1;
                        const path = pts.map((p, i) => `${(i / Math.max(1, n - 1)) * 300},${60 - ((p.pnl - lo) / rng) * 56}`).join(" ");
                        return <svg viewBox="0 0 300 60" width="100%" height="90" preserveAspectRatio="none" style={{ background: "var(--surface-2)", borderRadius: 8 }}>
                          <polyline points={path} fill="none" stroke="var(--acc)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
                        </svg>;
                      })()}
                      <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>
                        Cumulative PnL · {pnl.points.length} days · max drawdown {pnl.max_drawdown.toLocaleString()}
                      </div>
                      <div className="dx-head" style={{ marginTop: 12 }}><b>Eval</b>
                        <span className="mut">ret {pct(sel.returns)} · margin {bps(sel.margin)} · maxDD {pct(sel.drawdown)}</span></div>
                      {needsNeg(sel)
                        ? <div style={{ fontSize: 12, color: "var(--warn)" }}>↔ <b>Recommend negating</b> — metrics are negative (Sharpe {num(sel.sharpe)}). Submit the inverted signal (multiply by -1); drawdown/returns/margin flags don't apply until it's negated.</div>
                        : evalFlags(sel).length
                          ? <div style={{ fontSize: 12, color: "var(--bad)" }}>⚠ {evalFlags(sel).join(" · ")} — weak risk/reward, review before submitting.</div>
                          : <div style={{ fontSize: 12, color: "var(--ok)" }}>✓ Drawdown within returns and margin healthy.</div>}

                      <div className="dx-head" style={{ marginTop: 12 }}><b>Submission readiness</b>
                        {sel.alpha_id ? <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={() => runProdCorr([sel.alpha_id])} disabled={prodBusy}>
                          {prodBusy ? <><span className="spin" /> …</> : sel.prod_corr == null ? "Run prod-corr" : "Re-check prod-corr"}</button> : null}</div>
                      <div className="mut" style={{ fontSize: 12 }}>
                        Prod-corr: {sel.prod_corr == null ? <span style={{ color: "var(--warn)" }}>not checked</span>
                          : <b style={{ color: Math.abs(sel.prod_corr) < 0.7 ? "var(--ok)" : "var(--bad)" }}>{num(sel.prod_corr)}</b>}
                        {sel.prod_corr != null ? (Math.abs(sel.prod_corr) < 0.7 ? " — under 0.70 ✓" : " — ≥ 0.70 ✗") : ""}
                      </div>
                      {readiness(sel).length
                        ? <div className="mut" style={{ fontSize: 12, marginTop: 3 }}>Not ready: {readiness(sel).join(", ")}.</div>
                        : <div style={{ fontSize: 12, color: "var(--ok)", marginTop: 3 }}>✓ Passed all gates (metrics + prod-corr &lt; 0.70). BRAIN takes one submission at a time — submit it there yourself.</div>}
                      {yearly.length ? <>
                        <div className="dx-head" style={{ marginTop: 12 }}><b>Yearly stats</b></div>
                        <table style={{ fontSize: 11 }}><thead><tr>{Object.keys(yearly[0]).filter((k) => k !== "alpha_id").slice(0, 5).map((k) => <th key={k}>{k}</th>)}</tr></thead>
                          <tbody>{yearly.map((y, i) => <tr key={i}>{Object.keys(yearly[0]).filter((k) => k !== "alpha_id").slice(0, 5).map((k) => <td key={k}>{typeof y[k] === "number" ? (+y[k]).toFixed(2) : String(y[k])}</td>)}</tr>)}</tbody></table>
                      </> : null}
                    </>}
              </div>
            </>
          ) : (<>
          <div className="dx-head"><b>Operator insights</b><span className="mut">avg |fitness|, feeds generation</span></div>
          <div className="panel-scroll">
            {!insights.length ? <div className="empty">Not enough data yet (≥2 uses per operator).</div> :
              <table><thead><tr><th>operator</th><th>uses</th><th>avg</th></tr></thead>
                <tbody>{insights.map((o) => (
                  <tr key={o.operator}><td><code>{o.operator}</code></td><td>{o.count}</td>
                    <td><div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <div style={{ height: 6, borderRadius: 3, background: "var(--acc)", width: `${Math.round(60 * o.avg_fitness / maxFit)}px` }} />
                      <span className="mut">{o.avg_fitness}</span></div></td></tr>))}</tbody></table>}
          </div>
          </>)}
        </div>
      </div>
    </div>
  );
}
