import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";

type Candidate = {
  alpha_id: string; expr: string; region: string; delay: number; universe: string;
  sharpe: number | null; fitness: number | null; turnover: number | null;
  prod_corr: number | null; novelty: number | null; robustness: number | null;
  readiness_gaps: string[]; queue_status: string; record_id: number | null; score: number;
};

type Status = {
  date: string; daily_limit: number; submitted_today: number; remaining_today: number;
  queued: number; timezone: string;
};

export function SubmissionManager() {
  const { toast, toastErr } = useToast();
  const [status, setStatus] = useState<Status | null>(null);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [queue, setQueue] = useState<any[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [quota, setQuota] = useState(4);
  const [tab, setTab] = useState<"ready" | "queue">("ready");

  async function load() {
    const [s, c, q] = await Promise.all([
      api.get<Status>("/submission/status"),
      api.get<{ candidates: Candidate[] }>("/submission/candidates"),
      api.get<{ records: any[] }>("/submission/queue"),
    ]);
    if (s.error) return toastErr(s.error);
    setStatus(s); setQuota(s.daily_limit);
    setCandidates(c.candidates || []); setQueue(q.records || []);
  }
  useEffect(() => { load(); }, []);


  function toggle(id: string) {
    setSelected(prev => {
      const n = new Set(prev);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  }

  async function queueSelected() {
    const ids = [...selected];
    if (!ids.length) return toast("Select at least one ready alpha.", "warn");
    setBusy(true);
    const d = await api.post<any>("/submission/queue", { alpha_ids: ids });
    setBusy(false);
    if (d.error) return toastErr(d.error);
    setSelected(new Set());
    await load();
    if (d.errors?.length) toast(`${d.added?.length || 0} queued · ${d.errors.length} could not be queued.`, "warn");
    else toast(`${d.added?.length || 0} alpha(s) added to the submission queue.`, "ok");
    setTab("queue");
  }

  async function saveQuota() {
    setBusy(true);
    const d = await api.post<any>("/submission/settings", { daily_limit: Math.max(0, quota), timezone: status?.timezone || "Africa/Lagos" });
    setBusy(false);
    if (d.error) return toastErr(d.error);
    setStatus(d); toast("Local submission quota updated.", "ok");
  }

  async function submit(id: number) {
    if (!window.confirm("Submit this alpha to WorldQuant BRAIN now?")) return;
    setBusy(true);
    const d = await api.post<any>("/submission/submit", { record_id: id });
    setBusy(false);
    if (d.error) return toastErr(d.error);
    await load();
    toast(`${d.alpha_id} submitted successfully.`, "ok");
  }

  async function remove(id: number) {
    const d = await api.delete(`/submission/queue/${id}`);
    if (d?.error) return toastErr(d.error);
    await load();
  }

  async function retry(id: number) {
    const d = await api.post<any>("/submission/retry", { record_id: id });
    if (d.error) return toastErr(d.error);
    await load();
  }

  const barPct = status ? Math.min(100, (status.submitted_today / Math.max(1, status.daily_limit)) * 100) : 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: 1, minHeight: 0 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
        <div className="panel" style={{ padding: 12 }}>
          <div className="mut" style={{ fontSize: 11, textTransform: "uppercase" }}>Today's submissions</div>
          <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>{status?.submitted_today ?? "—"} / {status?.daily_limit ?? "—"}</div>
          <div style={{ height: 5, background: "var(--surface-2)", borderRadius: 5, marginTop: 8 }}>
            <div style={{ height: "100%", width: `${barPct}%`, background: "var(--acc)", borderRadius: 5 }} />
          </div>
        </div>
        <div className="panel" style={{ padding: 12 }}>
          <div className="mut" style={{ fontSize: 11, textTransform: "uppercase" }}>Remaining today</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: "var(--ok)", marginTop: 4 }}>{status?.remaining_today ?? "—"}</div>
        </div>
        <div className="panel" style={{ padding: 12 }}>
          <div className="mut" style={{ fontSize: 11, textTransform: "uppercase" }}>Queued</div>
          <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>{status?.queued ?? "—"}</div>
        </div>
        <div className="panel" style={{ padding: 12 }}>
          <div className="mut" style={{ fontSize: 11, textTransform: "uppercase" }}>Local quota</div>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <input type="number" min={0} value={quota} onChange={e => setQuota(+e.target.value || 0)} style={{ width: 75 }} />
            <button className="btn ghost sm" onClick={saveQuota} disabled={busy}>Save</button>
          </div>
          <div className="mut" style={{ fontSize: 10, marginTop: 5 }}>Guard only. BRAIN may enforce a different server-side limit.</div>
        </div>
      </div>

      <div className="panel" style={{ padding: 14, flex: 1, minHeight: 0, overflow: "auto" }}>
        <div className="dx-head">
          <b>Submission Manager</b>
          <span className="mut">simulation → verify → queue → submit</span>
          <span style={{ marginLeft: "auto" }}>
            {["ready", "queue"].map(t => (
              <span key={t} className={"pill" + (tab === t ? " on" : "")} onClick={() => setTab(t as any)}>{t === "ready" ? "Candidates" : "Queue"}</span>
            ))}
          </span>
        </div>

        {tab === "ready" ? (
          <>
            <div className="mut" style={{ fontSize: 12, margin: "6px 0 10px" }}>
              Only metric-passing alphas with a verified production correlation below 0.70 appear as ready.
              ACE never submits an alpha merely because it passed the simulation gate.
            </div>
            <div style={{ display: "flex", gap: 7, marginBottom: 8 }}>
              <button className="btn sm" onClick={queueSelected} disabled={busy || !selected.size}>＋ Queue selected ({selected.size})</button>
              <button className="btn ghost sm" onClick={load} disabled={busy}>↻ Refresh</button>
            </div>
            {!candidates.length ? <div className="empty">No metric-passing candidates yet. Simulate some alphas first.</div> :
              <table><thead><tr><th></th><th>Alpha</th><th>Fit</th><th>Sharpe</th><th>Turn</th><th>Novelty</th><th>Prod corr</th><th>Score</th><th>Status</th></tr></thead>
                <tbody>{candidates.map(c => {
                  const readyNow = !c.readiness_gaps?.length && !c.queue_status;
                  return <tr key={c.alpha_id}>
                    <td>{readyNow ? <input type="checkbox" checked={selected.has(c.alpha_id)} onChange={() => toggle(c.alpha_id)} /> : null}</td>
                    <td><code>{c.alpha_id || "—"}</code><div className="mut" style={{ fontSize: 10 }}>{(c.expr || "").slice(0, 48)}</div></td>
                    <td>{c.fitness == null ? "—" : Math.abs(c.fitness).toFixed(3)}</td>
                    <td>{c.sharpe == null ? "—" : Math.abs(c.sharpe).toFixed(3)}</td>
                    <td>{c.turnover == null ? "—" : (c.turnover * 100).toFixed(1) + "%"}</td>
                    <td>{c.novelty == null ? "—" : c.novelty.toFixed(2)}</td>
                    <td>{c.prod_corr == null ? "—" : Math.abs(c.prod_corr).toFixed(3)}</td>
                    <td><b>{c.score.toFixed(3)}</b></td>
                    <td>{c.queue_status ? <span className="badge">{c.queue_status}</span> :
                      c.readiness_gaps?.length ? <span className="mut" title={c.readiness_gaps.join("; ")}>Not ready</span> :
                      <span style={{ color: "var(--ok)" }}>Ready</span>}</td>
                  </tr>;
                })}</tbody></table>}
          </>
        ) : (
          <>
            <div className="mut" style={{ fontSize: 12, margin: "6px 0 10px" }}>
              Submission is manual. When today's local quota is full, queued alphas remain here for the next day.
            </div>
            {!queue.length ? <div className="empty">The submission queue is empty.</div> :
              <table><thead><tr><th>Alpha</th><th>Fit</th><th>Sharpe</th><th>Novelty</th><th>Queued</th><th>Status</th><th></th></tr></thead>
                <tbody>{queue.map(r => <tr key={r.id}>
                  <td><code>{r.alpha_id}</code><div className="mut" style={{ fontSize: 10 }}>{(r.expression || "").slice(0, 48)}</div></td>
                  <td>{r.fitness == null ? "—" : Math.abs(r.fitness).toFixed(3)}</td>
                  <td>{r.sharpe == null ? "—" : Math.abs(r.sharpe).toFixed(3)}</td>
                  <td>{r.novelty == null ? "—" : r.novelty.toFixed(2)}</td>
                  <td>{r.queued_for || "—"}</td>
                  <td>{r.status === "submitted" ? <span style={{ color: "var(--ok)" }}>Submitted</span> :
                    r.status === "error" ? <span style={{ color: "var(--bad)" }} title={r.error}>Error</span> :
                    r.status === "submitting" ? <span className="mut">Submitting…</span> :
                    <span className="badge">Queued</span>}</td>
                  <td style={{ whiteSpace: "nowrap" }}>
                    {r.status === "queued" && (status?.remaining_today || 0) > 0 ? <button className="btn sm" onClick={() => submit(r.id)} disabled={busy}>Submit</button> : null}
                    {r.status === "error" ? <button className="btn ghost sm" onClick={() => retry(r.id)} disabled={busy}>Retry</button> : null}
                    {r.status !== "submitted" ? <button className="btn ghost sm" onClick={() => remove(r.id)} disabled={busy} style={{ marginLeft: 4 }}>Remove</button> : null}
                  </td>
                </tr>)}</tbody></table>}
          </>
        )}
      </div>
    </div>
  );
}
