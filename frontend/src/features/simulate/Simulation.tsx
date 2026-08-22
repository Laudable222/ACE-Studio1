import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";

const num = (v: any) => (v == null ? "—" : (+v).toFixed(3));

// Simulation + the success gate. Alphas run across the chosen universes/neutralizations;
// each is judged against every gate metric (delay 0 is strict on Sharpe/Fitness). Tags are
// the user's own. Correlations require the (optional) submission check.
//
// All configuration is persisted, so leaving the screen and returning (or a full reload)
// keeps every setting — it never resets to the data-fetch context. A running job is
// reconnected on mount so its live progress reappears instead of vanishing.
export function Simulation() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();

  const [expr, setExpr] = usePersistentState("sim:expr", (R.pending || []).join("\n"));
  const [expOrigin, setExpOrigin] = usePersistentState<number | null>("sim:exporigin", null);   // Experiment id the CURRENT unedited expr text came from, if any
  const [expExperiment, setExpExperiment] = useState<any>(null);   // that experiment's hypothesis/fields, fetched for display
  const [showExpFields, setShowExpFields] = useState(false);
  const [unis, setUnis] = usePersistentState<string[]>("sim:unis", [R.ctx.universe]);
  const [neuts, setNeuts] = usePersistentState<string[]>("sim:neuts", ["INDUSTRY"]);
  const [decay, setDecay] = usePersistentState("sim:decay", 4);
  const [trunc, setTrunc] = usePersistentState("sim:trunc", 0.08);
  const [conc, setConc] = usePersistentState("sim:conc", 3);
  const [multi, setMulti] = usePersistentState("sim:multi", 10);
  const [gate, setGate] = usePersistentState("sim:gate", { sharpe: 1.58, fitness: 1.0, max_turnover: 0.7, max_corr: 0.7 });
  const [winAbove, setWinAbove] = usePersistentState("sim:winabove", 2.0);
  const [checkSub, setCheckSub] = usePersistentState("sim:checksub", false);
  const [rangeFrom, setRangeFrom] = usePersistentState("sim:rangefrom", 1);
  const [rangeTo, setRangeTo] = usePersistentState("sim:rangeto", 0);   // 0 = to the end
  // engine settings — every one is toggleable and persisted
  const [pasteur, setPasteur] = usePersistentState("sim:pasteur", "ON");
  const [nanH, setNanH] = usePersistentState("sim:nan", "OFF");
  const [maxTrade, setMaxTrade] = usePersistentState("sim:maxtrade", "OFF");
  // tags live in Settings' localStorage keys (shared)
  const [tag, setTag] = useState(() => localStorage.getItem("ace2-tag") || "");
  const [winnerTag, setWinnerTag] = useState(() => localStorage.getItem("ace2-winnertag") || "");

  const [busy, setBusy] = useState(false);
  const [prog, setProg] = useState<{ done: number; total: number; msg: string } | null>(null);
  const [res, setRes] = usePersistentState<any | null>("sim:res", null);
  const [jobId, setJobId] = usePersistentState<string>("sim:job", "");
  const jobRef = useRef<string>(jobId);
  jobRef.current = jobId;

  const universes = R.universes(R.ctx.instrument, R.ctx.region, R.ctx.delay);
  const neutOpts = useMemo(() => {
    const recs = (R.options || []).filter((o) => o.instrument === R.ctx.instrument && o.region === R.ctx.region);
    return [...new Set(recs.flatMap((o) => o.neutralizations))];
  }, [R.options, R.ctx]);

  // Gate defaults follow delay (2.69/1.5 at delay 0) — but only re-fetch when the delay
  // actually CHANGES, so a persisted custom gate isn't clobbered on every mount.
  const gateDelay = useRef<number | null>(null);
  useEffect(() => {
    if (gateDelay.current === R.ctx.delay) return;
    gateDelay.current = R.ctx.delay;
    api.get<any>(`/simulate/gate?delay=${R.ctx.delay}`).then((g) =>
      setGate({ sharpe: g.sharpe, fitness: g.fitness, max_turnover: g.max_turnover, max_corr: g.max_corr }));
  }, [R.ctx.delay]);

  // Apply a handoff from Template Studio / Generation ONLY when it actually changes — never
  // re-overwrite the user's edits on a plain remount.
  const lastPending = useRef<string>("");
  useEffect(() => {
    const key = (R.pending || []).join("\n");
    if (key && key !== lastPending.current) { lastPending.current = key; setExpr(key); setExpOrigin(R.pendingExperimentId ?? null); }
  }, [R.pending]);

  // While a batch is linked to an experiment (see expOrigin above), pull its hypothesis and
  // mapped fields so you can see exactly what you're about to run without leaving this screen.
  useEffect(() => {
    if (!expOrigin) { setExpExperiment(null); return; }
    setShowExpFields(false);
    let cancelled = false;
    api.get<any>("/discovery/experiments").then((d) => {
      if (cancelled) return;
      setExpExperiment((d.experiments || []).find((e: any) => e.id === expOrigin) || null);
    });
    return () => { cancelled = true; };
  }, [expOrigin]);

  // Keep chosen universes valid for the current region without wiping the user's selection:
  // drop any that no longer exist; if none remain, fall back to the context universe.
  useEffect(() => {
    setUnis((prev) => {
      const keep = prev.filter((u) => universes.includes(u));
      return keep.length ? keep : [universes.includes(R.ctx.universe) ? R.ctx.universe : (universes[0] || "TOP3000")];
    });
  }, [R.ctx.region]);

  // Reconnect to a job that's still running (survives navigation + reload).
  useEffect(() => {
    if (jobId) { setBusy(true); poll(jobId); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (arr: string[], v: string, set: (x: string[]) => void) =>
    set(arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const flip = (v: string, on: string, off: string) => (v === on ? off : on);

  const allExprs = expr.split("\n").map((s) => s.trim()).filter(Boolean);
  // Simulate only a slice (1-based, inclusive); rangeTo 0 means "to the end".
  const from = Math.max(1, rangeFrom);
  const to = rangeTo > 0 ? rangeTo : allExprs.length;
  const exprs = allExprs.slice(from - 1, to);

  async function poll(id: string) {
    let s: any = {};
    for (; ;) {
      s = await api.get(`/simulate/jobs/${id}`);
      if (s.error && s.status === undefined) break;   // job gone (e.g. backend restarted)
      if (s.total || s.done) setProg({ done: s.done || 0, total: s.total || 0, msg: s.message || "" });
      if (s.status !== "running") break;
      await new Promise((r) => setTimeout(r, 1400));
    }
    setBusy(false); setProg(null); setJobId("");
    if (s.status === "error") return toastErr(s.error);
    if (!s.result) return;   // job vanished; nothing to show
    setRes(s.result);
    if (s.result.stopped_early) toast(s.result.stopped_early, "warn");
    else if (s.result.passed) toast(`${s.result.passed} of ${s.result.simulated} passed the gate · ${s.result.tagged || 0} tagged.`, "ok");
    else toast(`${s.result.simulated} simulated · none passed the gate yet.`, "warn");
  }

  function retryFailed() {
    const failed = [...new Set((res?.results || []).filter((r: any) => !r.passed).map((r: any) => String(r.expr || "")).filter(Boolean))] as string[];
    if (!failed.length) return toast("No failed configs to retry.", "warn");
    setExpr(failed.join("\n"));
    run(failed);
  }

  const [evolveBusy, setEvolveBusy] = useState("");
  async function evolveAlpha(alphaId: string) {
    // create_family() is idempotent on an open family for the same parent alpha, so clicking
    // this again for an already-evolving alpha just reopens it rather than duplicating.
    setEvolveBusy(alphaId);
    const d = await api.post<any>("/evolution/families", { alpha_id: alphaId, budget: 30 });
    setEvolveBusy("");
    if (d.error) return toastErr(d.error);
    R.setPendingFamilyId(d.id);
    nav("/evolution");
  }

  async function run(override?: string[]) {
    const list = override && override.length ? override : exprs;
    if (!list.length) return toast("Add expressions (from Generation, or paste).", "warn");
    if (!unis.length || !neuts.length) return toast("Pick at least one universe and neutralization.", "warn");
    localStorage.setItem("ace2-tag", tag); localStorage.setItem("ace2-winnertag", winnerTag);
    setBusy(true); setRes(null); setProg({ done: 0, total: 0, msg: "starting…" });
    const start = await api.post<{ job_id: string; error?: string }>("/simulate/run", {
      expressions: list, region: R.ctx.region, delay: R.ctx.delay, universes: unis, neutralizations: neuts,
      decay, truncation: trunc, concurrency: conc, limit_of_multi: multi,
      pasteurization: pasteur, nan_handling: nanH, max_trade: maxTrade, unit_handling: "VERIFY",
      min_sharpe: gate.sharpe, min_fitness: gate.fitness,
      max_turnover: gate.max_turnover, max_corr: gate.max_corr, tag, winner_tag: winnerTag,
      tag_winners_above: winAbove, check_submission: checkSub,
      experiment_id: expOrigin ?? undefined,
    });
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
    setJobId(start.job_id);
    poll(start.job_id);
  }

  async function stop() { if (jobRef.current) await api.post(`/simulate/jobs/${jobRef.current}/cancel`); }

  // ── cross-region sweep ──────────────────────────────────────────────────────────────
  const allRegions = R.regions(R.ctx.instrument);
  const [sweepRegions, setSweepRegions] = usePersistentState<string[]>("sim:sweepregions", []);
  const [sweepBusy, setSweepBusy] = useState(false);
  const [sweepProg, setSweepProg] = useState("");
  const [sweepRes, setSweepRes] = usePersistentState<any[] | null>("sim:sweepres", null);

  async function runSweep() {
    if (!exprs.length) return toast("Add expressions to sweep.", "warn");
    if (!sweepRegions.length) return toast("Pick target regions to sweep.", "warn");
    setSweepBusy(true); setSweepRes(null); setSweepProg("starting…");
    const start = await api.post<any>("/simulate/sweep", {
      expressions: exprs, regions: sweepRegions, delay: R.ctx.delay, instrument: R.ctx.instrument,
      neutralizations: neuts, decay, truncation: trunc, concurrency: conc, limit_of_multi: multi, tag, winner_tag: winnerTag,
      home_region: R.ctx.region,
    });
    if (start.error || !start.job_id) { setSweepBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {};
    for (; ;) { s = await api.get(`/simulate/jobs/${start.job_id}`); if (s.message) setSweepProg(`${s.message}${s.total ? ` · ${s.done}/${s.total}` : ""}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1600)); }
    setSweepBusy(false); setSweepProg("");
    if (s.status !== "done") return toastErr(s.error || s.status);
    setSweepRes(s.result?.regions || []);
    const passed = (s.result?.regions || []).reduce((a: number, r: any) => a + (r.passed || 0), 0);
    toast(`Swept ${sweepRegions.length} region(s) · ${passed} passed the gate.`, passed ? "ok" : "warn");
  }

  // ── batch queue (run several expression sets sequentially, unattended) ───────────────
  const [queue, setQueue] = usePersistentState<{ label: string; expressions: string[] }[]>("sim:queue", []);
  const [queueBusy, setQueueBusy] = useState(false);
  const [queueRes, setQueueRes] = usePersistentState<any[] | null>("sim:queueres", null);
  function enqueue() {
    if (!exprs.length) return toast("Nothing to queue.", "warn");
    setQueue((q) => [...q, { label: `${R.ctx.region} · ${exprs.length} expr · #${q.length + 1}`, expressions: exprs }]);
    toast("Added to the batch queue.");
  }
  async function runQueue() {
    if (!queue.length) return toast("The queue is empty.", "warn");
    setQueueBusy(true); setQueueRes(null);
    const start = await api.post<any>("/simulate/batch", {
      batches: queue, region: R.ctx.region, delay: R.ctx.delay, universes: unis, neutralizations: neuts,
      decay, truncation: trunc, concurrency: conc, limit_of_multi: multi, min_sharpe: gate.sharpe, min_fitness: gate.fitness, tag, winner_tag: winnerTag,
    });
    if (start.error || !start.job_id) { setQueueBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/simulate/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 2000)); }
    setQueueBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    setQueueRes(s.result?.batches || []);
    toast("Batch queue complete.", "ok");
  }

  const [wfBusy, setWfBusy] = useState(false);
  const [wfRes, setWfRes] = usePersistentState<any[] | null>("sim:wfres", null);
  async function runWalkForward() {
    if (!exprs.length) return toast("Add expressions first.", "warn");
    setWfBusy(true); setWfRes(null);
    const start = await api.post<any>("/simulate/walkforward", {
      expressions: exprs, regions: [R.ctx.region], instrument: R.ctx.instrument, neutralizations: neuts,
      decay, truncation: trunc, concurrency: conc, limit_of_multi: multi, tag, winner_tag: winnerTag,
    });
    if (start.error || !start.job_id) { setWfBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/simulate/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1600)); }
    setWfBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    setWfRes(s.result?.delays || []);
    toast("Walk-forward complete (delay 1 & 0).", "ok");
  }

  const Pill = ({ on, label, onClick, title }: { on: boolean; label: string; onClick: () => void; title?: string }) =>
    <span className={"pill" + (on ? " on" : "")} onClick={onClick} title={title}>{label}</span>;

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      {/* settings */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>Simulation</b>
          <span className="mut">{R.ctx.region} · D{R.ctx.delay} · {exprs.length}{exprs.length !== allExprs.length ? ` of ${allExprs.length}` : ""} expr</span>
          {busy ? <span className="badge ok" style={{ marginLeft: "auto" }}>running</span> : null}</div>
        <label className="fld"><span>Expressions (One Per Line — From Generation){expOrigin ? <span className="badge ok" style={{ marginLeft: 6 }}>linked to experiment #{expOrigin}</span> : null}</span>
          <textarea value={expr} onChange={(e) => { setExpr(e.target.value); setExpOrigin(null); }} style={{ minHeight: 92 }} /></label>

        {expExperiment ? <div className="mut" style={{ fontSize: 11, marginTop: 6, padding: 8, background: "var(--surface-2)", borderRadius: 7 }}>
          <div style={{ color: "var(--fg)", fontSize: 12 }}>{expExperiment.hypothesis?.statement || expExperiment.name}</div>
          {expExperiment.hypothesis?.mechanism ? <div style={{ marginTop: 3 }}>{expExperiment.hypothesis.mechanism}</div> : null}
          <div style={{ marginTop: 4, cursor: expExperiment.field_ids?.length ? "pointer" : "default", textDecoration: expExperiment.field_ids?.length ? "underline" : "none" }}
            onClick={() => expExperiment.field_ids?.length && setShowExpFields((v) => !v)}>
            {showExpFields ? "▾" : "▸"} {expExperiment.field_ids?.length || 0} field{expExperiment.field_ids?.length === 1 ? "" : "s"} mapped to this hypothesis</div>
          {showExpFields ? (expExperiment.field_ids || []).map((f: any, i: number) =>
            <div key={i} style={{ padding: "1px 0" }}><code>{typeof f === "string" ? f : f.id}</code>{typeof f !== "string" && f.dataset_id ? <> — {f.dataset_id}</> : null}</div>) : null}
        </div> : null}

        <label style={{ fontSize: 11, color: "var(--mut)", marginTop: 8, display: "block" }}>Universes</label>
        <div className="dx-filters wrap">{universes.map((u) =>
          <span key={u} className={"pill" + (unis.includes(u) ? " on" : "")} onClick={() => toggle(unis, u, setUnis)}>{u}</span>)}</div>
        <label style={{ fontSize: 11, color: "var(--mut)" }}>Neutralizations</label>
        <div className="dx-filters wrap">{neutOpts.map((nu) =>
          <span key={nu} className={"pill" + (neuts.includes(nu) ? " on" : "")} onClick={() => toggle(neuts, nu, setNeuts)}>{nu}</span>)}</div>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 8, marginTop: 8 }}>
          <label className="fld" title="Exponential decay applied to the alpha's signal over time, in days — higher smooths the signal more and tends to reduce turnover."><span>Decay</span><NumberInput min={0} fallback={4} value={decay} onChange={setDecay} /></label>
          <label className="fld" title="Caps each position's weight as a fraction of the portfolio, so no single instrument can dominate."><span>Truncation</span><NumberInput step="0.01" min={0} fallback={0.08} value={trunc} onChange={setTrunc} /></label>
          <label className="fld" title="How many batches BRAIN works on at once for this run — higher finishes faster but uses more of your concurrency budget."><span>Concurrency (1–8)</span><NumberInput min={1} max={8} fallback={3} value={conc} onChange={setConc} /></label>
          <label className="fld" title="How many alphas BRAIN simulates together in one batch — 2 to 10 is BRAIN's own platform limit, not an app restriction."><span>Multi-batch (2–10)</span><NumberInput min={2} max={10} fallback={10} value={multi} onChange={setMulti} /></label>
          <label className="fld" title="Minimum absolute Sharpe ratio required to pass the gate. Leave at 0 to use the built-in threshold for this delay (2.69 at delay 0, else 1.58)."><span>|Sharpe| ≥ {R.ctx.delay === 0 ? "(D0)" : ""}</span><NumberInput step="0.01" min={0} fallback={0} value={gate.sharpe} onChange={(v) => setGate({ ...gate, sharpe: v })} /></label>
          <label className="fld" title="Minimum absolute Fitness score required to pass the gate. Leave at 0 to use the built-in threshold for this delay (1.5 at delay 0, else 1.0)."><span>|Fitness| ≥</span><NumberInput step="0.1" min={0} fallback={0} value={gate.fitness} onChange={(v) => setGate({ ...gate, fitness: v })} /></label>
        </div>

        <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 10 }}>Engine Settings</div>
        <div className="dx-filters wrap">
          <Pill on={pasteur === "ON"} label={`Pasteurization: ${pasteur}`} onClick={() => setPasteur((v) => flip(v, "ON", "OFF"))}
            title="Aligns each alpha to the universe used at simulation time." />
          <Pill on={nanH === "ON"} label={`NaN handling: ${nanH}`} onClick={() => setNanH((v) => flip(v, "ON", "OFF"))}
            title="ON treats NaN as 0; OFF leaves gaps as missing." />
          <Pill on={maxTrade === "ON"} label={`Max trade: ${maxTrade}`} onClick={() => setMaxTrade((v) => flip(v, "ON", "OFF"))}
            title="Caps per-instrument trading when ON." />
          <span className="pill on" style={{ cursor: "default" }} title="Unit handling is always VERIFY (enforces dimensional consistency).">Unit handling: VERIFY</span>
        </div>

        <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 10 }}>Tags (Your Own) & Checks</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8 }}>
          <label className="fld" title="Applied to every alpha simulated in this batch, pass or fail. Your own tag — never sent anywhere else."><span>Tag Every Alpha</span><input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="e.g. my_news" /></label>
          <label className="fld" title="Applied only to alphas whose |fitness| clears the threshold on the right."><span>Winner Tag</span><input value={winnerTag} onChange={(e) => setWinnerTag(e.target.value)} placeholder="e.g. my_winner" /></label>
          <label className="fld" title="The |fitness| threshold that triggers the Winner Tag."><span>Winner |fit| ≥</span><NumberInput step="0.1" min={0} fallback={2} value={winAbove} onChange={setWinAbove} style={{ width: 80 }} /></label>
        </div>
        <div className="dx-filters" style={{ marginTop: 8 }}>
          <span className={"pill" + (checkSub ? " on" : "")} onClick={() => setCheckSub((v) => !v)}
            title="Runs BRAIN's submission checks so self/prod/powerpool correlations join the gate. Slower.">Correlation & Submission Checks</span>
        </div>

        <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 10 }}>Simulate A Range (Of {allExprs.length})</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8, alignItems: "end" }}>
          <label className="fld"><span>From (1-based)</span><NumberInput min={1} fallback={1} value={rangeFrom} onChange={setRangeFrom} /></label>
          <label className="fld"><span>To (0 = end)</span><NumberInput min={0} fallback={0} value={rangeTo} onChange={setRangeTo} /></label>
          <button className="btn ghost sm" onClick={() => { setRangeFrom(1); setRangeTo(0); }}>All</button>
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button className="btn" style={{ flex: 1 }} onClick={() => run()} disabled={busy}>
            {busy ? <><span className="spin" /> Simulating…</> : "▶ Run Simulation"}</button>
          {busy ? <button className="btn ghost" onClick={stop}>Stop</button> : null}
        </div>
        {prog ? <div className="mut" style={{ fontSize: 12, marginTop: 8 }}>
          <span className="spin" /> {prog.msg} {prog.total ? `· ${prog.done}/${prog.total}` : ""}</div> : null}
      </div>

      {/* results */}
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>Results</b>
          {res ? <span className="mut">{res.passed}/{res.simulated} passed the gate</span> : null}
          {res && res.results?.some((r: any) => !r.passed) ?
            <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={retryFailed} disabled={busy}>
              Retry {[...new Set(res.results.filter((r: any) => !r.passed).map((r: any) => r.expr))].length} failed</button> : null}</div>
        {res?.stopped_early ? <div className="mut" style={{ fontSize: 11, padding: 8, margin: "8px 0", background: "var(--bad-weak)", color: "var(--bad)", borderRadius: 7 }}>
          ⚠ {res.stopped_early}</div> : null}
        <div className="panel-scroll">
          {!res ? <div className="empty">Run a simulation. Each alpha is judged against every gate metric; a green ✓ means it passed all of them.</div> :
            !res.results?.length ? <div className="empty">Nothing simulated (check the log / session).</div> :
              <table><thead><tr><th></th><th>Alpha</th><th>Sharpe</th><th>Fit</th><th>Turn</th><th>Uni / Neut</th><th>Why Not</th><th></th></tr></thead>
                <tbody>{res.results.map((r: any, i: number) => (
                  <tr key={i}>
                    <td>{r.passed ? <span style={{ color: "var(--ok)" }}>✓</span> : <span style={{ color: "var(--bad)" }}>✗</span>}</td>
                    <td>{r.alpha_id ? <a href={`https://platform.worldquantbrain.com/alpha/${r.alpha_id}`} target="_blank" rel="noopener"><code>{r.alpha_id}</code></a> : "—"}
                      <div className="mut sub">{(r.expr || "").slice(0, 40)}</div></td>
                    <td>{num(r.sharpe)}</td><td>{num(r.fitness)}</td><td>{r.turnover == null ? "—" : (r.turnover * 100).toFixed(0) + "%"}</td>
                    <td className="mut" style={{ fontSize: 11 }}>{r.universe}<br />{r.neutralization}</td>
                    <td className="mut" style={{ fontSize: 11 }}>{(r.reasons || []).join(", ")}</td>
                    <td>{!r.passed && r.alpha_id ? <button className="btn ghost sm" title="Diagnose why it failed and propose controlled variants"
                      onClick={() => evolveAlpha(r.alpha_id)} disabled={evolveBusy === r.alpha_id}>{evolveBusy === r.alpha_id ? <span className="spin" /> : "🧬 Evolve"}</button> : null}</td>
                  </tr>))}</tbody></table>}

          <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 14 }}>Cross-Region Sweep</div>
          <div className="mut" style={{ fontSize: 12, marginBottom: 6 }}>Run the same {exprs.length} expression(s) across other regions to diversify fast. Each region first has its <b>datafields verified against BRAIN</b> (in a valid universe); regions missing a field are skipped, not guessed.</div>
          <div className="dx-filters wrap">
            {allRegions.filter((rg) => rg !== R.ctx.region).map((rg) =>
              <span key={rg} className={"pill" + (sweepRegions.includes(rg) ? " on" : "")} onClick={() => setSweepRegions((x) => x.includes(rg) ? x.filter((y) => y !== rg) : [...x, rg])}>{rg}</span>)}
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn ghost sm" onClick={runSweep} disabled={sweepBusy || busy}>{sweepBusy ? <><span className="spin" /> Sweeping…</> : "▶ Run Cross-Region Sweep"}</button>
          </div>
          {sweepProg ? <div className="mut" style={{ fontSize: 12, marginTop: 6 }}><span className="spin" /> {sweepProg}</div> : null}
          {sweepRes ? <table style={{ marginTop: 8 }}><thead><tr><th>Region</th><th>Universe</th><th>Passed</th><th>Simulated</th></tr></thead>
            <tbody>{sweepRes.map((rg: any, i: number) => (
              <tr key={i}><td><b>{rg.region}</b></td><td className="mut">{rg.universe || "—"}</td>
                <td style={{ color: rg.passed ? "var(--ok)" : undefined }}>{rg.error || rg.skipped ? "—" : rg.passed}</td>
                <td className="mut" style={{ fontSize: 11 }}>
                  {rg.error ? <span style={{ color: "var(--bad)" }}>{rg.error}</span>
                    : rg.skipped ? <span style={{ color: "var(--warn)" }}>skipped · missing {(rg.missing || []).join(", ")}</span>
                      : rg.simulated}</td></tr>))}</tbody></table> : null}

          <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 14 }}>Batch Queue (Run Sequentially, Unattended)</div>
          <div className="dx-filters">
            <button className="btn ghost sm" onClick={enqueue}>+ Add Current ({exprs.length})</button>
            <button className="btn ghost sm" onClick={() => { setQueue([]); setQueueRes(null); }} disabled={!queue.length}>Clear Queue</button>
            <button className="btn sm" onClick={runQueue} disabled={queueBusy || busy || !queue.length}>{queueBusy ? <><span className="spin" /> Running…</> : `▶ Run Queue (${queue.length})`}</button>
          </div>
          {queue.length ? <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>{queue.map((b, i) => <div key={i}>{i + 1}. {b.label}</div>)}</div> : null}
          {queueRes ? <table style={{ marginTop: 6 }}><thead><tr><th>Batch</th><th>Passed</th><th>Simulated</th></tr></thead>
            <tbody>{queueRes.map((b: any, i: number) => (
              <tr key={i}><td>{b.label}</td><td style={{ color: b.passed ? "var(--ok)" : undefined }}>{b.error || b.skipped ? "—" : b.passed}</td>
                <td className="mut">{b.error ? <span style={{ color: "var(--bad)", fontSize: 11 }}>{b.error}</span> : b.skipped || b.simulated}</td></tr>))}</tbody></table> : null}

          <div className="section" style={{ fontSize: 11, color: "var(--mut)", textTransform: "uppercase", letterSpacing: ".5px", marginTop: 14 }}>Walk-Forward (Both Delays)</div>
          <div className="mut" style={{ fontSize: 12, marginBottom: 6 }}>Run the expressions at delay 1 AND delay 0 in {R.ctx.region} — an alpha that passes both is far more robust.</div>
          <button className="btn ghost sm" onClick={runWalkForward} disabled={wfBusy || busy}>{wfBusy ? <><span className="spin" /> Running…</> : "▶ Walk-Forward Check"}</button>
          {wfRes ? <table style={{ marginTop: 8 }}><thead><tr><th>Delay</th><th>Universe</th><th>Passed</th><th>Simulated</th></tr></thead>
            <tbody>{wfRes.map((d: any, i: number) => (
              <tr key={i}><td><b>D{d.delay}</b></td><td className="mut">{d.universe || "—"}</td>
                <td style={{ color: d.passed ? "var(--ok)" : undefined }}>{d.error || d.skipped ? "—" : d.passed}</td>
                <td className="mut">{d.error ? <span style={{ color: "var(--bad)", fontSize: 11 }}>{d.error}</span> : d.skipped ? <span style={{ fontSize: 11 }}>{d.skipped}</span> : d.simulated}</td></tr>))}</tbody></table> : null}
        </div>
      </div>
    </div>
  );
}
