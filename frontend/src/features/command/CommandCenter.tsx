import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";

interface Summary {
  total: number; passed: number; success_rate: number;
  diversity: { operators: number; regions: number };
  best: { alpha_id: string; expr: string; fitness: number; sharpe: number }[];
  operator_insights: { operator: string; count: number; avg_fitness: number }[];
}

// The Command Center is now wired to the live success-rate engine: it shows how the
// research is actually doing and points at the next best move.
export function CommandCenter() {
  const nav = useNavigate();
  const [s, setS] = useState<Summary | null>(null);
  const [up, setUp] = useState(false);

  useEffect(() => {
    // only accept a well-formed summary; an error response (backend down) has no diversity
    api.get<Summary & { error?: string }>("/analytics/summary").then((d) => { if (!d.error) setS(d); });
    api.get<{ version?: string; error?: string }>("/health/ping").then((d) => setUp(!d.error));
  }, []);

  const rate = s ? Math.round(s.success_rate * 100) : null;
  const kpis = [
    { label: "Success rate", value: rate == null ? "—" : `${rate}%`, hint: "passed the gate / simulated" },
    { label: "Submittable", value: s?.passed ?? "—", hint: "alphas clearing every metric" },
    { label: "Simulated", value: s?.total ?? "—", hint: "total judged so far" },
    { label: "Diversity", value: s?.diversity?.operators ?? "—", hint: "distinct operators explored" },
  ];

  return (
    <div className="dx-stack" style={{ gap: 14 }}>
      <div className="panel" style={{ padding: 16 }}>
        <div style={{ fontSize: 17, fontWeight: 600 }}>Command Center</div>
        <div className="mut" style={{ marginTop: 4 }}>
          Backend {up ? "online" : "offline"}. {s?.total ? `${s.passed} of ${s.total} alphas have cleared the full success gate.` : "Run a simulation to start building your success record."}
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 14 }}>
        {kpis.map((k) => (
          <div key={k.label} className="panel" style={{ padding: 16 }}>
            <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px" }}>{k.label}</div>
            <div style={{ fontSize: 28, fontWeight: 700, marginTop: 6, color: "var(--acc)" }}>{k.value}</div>
            <div className="mut" style={{ fontSize: 11, marginTop: 4 }}>{k.hint}</div>
          </div>
        ))}
      </div>

      <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
        <div className="panel" style={{ padding: 16 }}>
          <div className="dx-head"><b>Best alphas</b><span className="mut">passed the gate, by |fitness|</span></div>
          <div className="panel-scroll">
            {!s?.best?.length ? <div className="empty">No gate-passing alphas yet. Generate → Simulate to fill this in.</div> :
              <table><thead><tr><th>alpha</th><th>Sharpe</th><th>Fit</th></tr></thead>
                <tbody>{s.best.map((b, i) => (
                  <tr key={i}><td><code>{b.alpha_id}</code><div className="mut" style={{ fontSize: 11 }}>{(b.expr || "").slice(0, 38)}</div></td>
                    <td>{b.sharpe?.toFixed(2)}</td><td>{b.fitness?.toFixed(2)}</td></tr>))}</tbody></table>}
          </div>
        </div>

        <div className="panel" style={{ padding: 16 }}>
          <div className="dx-head"><b>Next best actions</b></div>
          <div className="panel-scroll">
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <ActionCard onClick={() => nav("/research")} title="Research a new idea"
                body="Turn a mechanism (or a research paper) into grounded hypotheses in the Research Lab." />
              <ActionCard onClick={() => nav("/generate")} title="Generate with more diversity"
                body={`You have explored ${s?.diversity?.operators ?? 0} distinct operators — the diversity engine will steer you toward the ones you haven't used.`} />
              <ActionCard onClick={() => nav("/knowledge")} title="Go cross-region"
                body="The Knowledge Graph shows where your best datasets also exist in other regions — replay a winning idea there." />
              <ActionCard onClick={() => nav("/portfolio")} title="Find an uncorrelated set"
                body="Correlation & Portfolio finds the largest group of your passing alphas you can submit together." />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ActionCard({ title, body, onClick }: { title: string; body: string; onClick: () => void }) {
  return (
    <div className="panel" onClick={onClick}
      style={{ padding: "11px 13px", boxShadow: "none", cursor: "pointer", border: "1px solid var(--line)" }}>
      <div style={{ fontWeight: 600, color: "var(--acc)" }}>{title}</div>
      <div className="mut" style={{ fontSize: 12, marginTop: 3 }}>{body}</div>
    </div>
  );
}
