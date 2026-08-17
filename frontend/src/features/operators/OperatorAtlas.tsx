import { useEffect, useMemo, useState } from "react";
import { api } from "../../lib/api";
import { usePersistentState } from "../../lib/persist";
import { useToast } from "../../lib/toast";

interface Param { name: string; kind: string; required: boolean; default: string; }
interface Op { name: string; scope: string; signature: string; params: Param[]; example: string; notes: string; user_edited: boolean; }
interface Insight { operator: string; count: number; avg_fitness: number; }

// Operator Atlas / Operator Lab: every operator with its signature, a canonical CORRECT-usage
// example, and notes — the single source of truth the LLM prompts read. Seeded from your
// account's operator definitions (keyword vs positional parsed from the signature) plus curated
// examples for the ones that are easy to misuse. Edit any example/notes and it's used everywhere;
// your edits survive re-seeding.
export function OperatorAtlas() {
  const { toast, toastErr } = useToast();
  const [ops, setOps] = useState<Op[]>([]);
  const [use, setUse] = useState<Record<string, Insight>>({});
  const [q, setQ] = usePersistentState("ops:q", "");
  const [scope, setScope] = usePersistentState("ops:scope", "REGULAR");   // REGULAR|SELECTION|COMBO|ALL
  const [onlyEdited, setOnlyEdited] = usePersistentState("ops:edited", false);
  const [onlyNoted, setOnlyNoted] = usePersistentState("ops:noted", false);
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<{ name: string; example: string; notes: string } | null>(null);
  const [sandbox, setSandbox] = usePersistentState("ops:sandbox", "ts_rank(close, 20)");
  const [check, setCheck] = useState<{ ok: boolean; issues: { code: string; message: string }[] } | null>(null);
  const [checking, setChecking] = useState(false);

  async function runCheck() {
    if (!sandbox.trim()) return;
    setChecking(true);
    const r = await api.post<any>("/operators/check", { expression: sandbox });
    setChecking(false);
    if (r.error) return toastErr(r.error);
    setCheck({ ok: r.ok, issues: r.issues || [] });
  }

  const load = () => api.get<{ operators: Op[] }>("/operators/list").then((d) => setOps(d.operators || []));
  useEffect(() => {
    load();
    api.get<{ operator_insights: Insight[] }>("/analytics/summary").then((d) => {
      const m: Record<string, Insight> = {};
      (d.operator_insights || []).forEach((i) => { m[i.operator] = i; });
      setUse(m);
    });
  }, []);

  async function seed() {
    setBusy(true);
    const r = await api.post<any>("/operators/seed");
    setBusy(false);
    if (r.error) return toastErr(r.error);
    toast(`Reference rebuilt — ${r.added} added, ${r.updated} refreshed.`, "ok");
    load();
  }

  async function save() {
    if (!edit) return;
    const r = await api.post<any>(`/operators/op/${encodeURIComponent(edit.name)}`, { example: edit.example, notes: edit.notes });
    if (r.error) return toastErr(r.error);
    toast(`${edit.name} updated — the LLM will use it.`, "ok");
    setEdit(null); load();
  }

  const view = useMemo(() => ops.filter((o) => {
    if (scope !== "ALL" && (o.scope || "REGULAR") !== scope) return false;
    if (q && !o.name.toLowerCase().includes(q.toLowerCase())) return false;
    if (onlyEdited && !o.user_edited) return false;
    if (onlyNoted && !(o.notes || "").trim()) return false;
    return true;
  }), [ops, q, scope, onlyEdited, onlyNoted]);
  const scopeCounts = useMemo(() => {
    const m: Record<string, number> = { REGULAR: 0, SELECTION: 0, COMBO: 0 };
    ops.forEach((o) => { const s = o.scope || "REGULAR"; m[s] = (m[s] || 0) + 1; });
    return m;
  }, [ops]);

  const edited = ops.filter((o) => o.user_edited).length;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: 1, minHeight: 0 }}>
      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head">
          <b>Operator Lab</b>
          <span className="mut">{ops.length} operators · {edited} edited by you</span>
          <input placeholder="filter…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 180, marginLeft: "auto" }} />
          {(["REGULAR", "SELECTION", "COMBO", "ALL"] as const).map((s) =>
            <span key={s} className={"pill" + (scope === s ? " on" : "")} onClick={() => setScope(s)}>
              {s.toLowerCase()}{s !== "ALL" ? ` ${scopeCounts[s] || 0}` : ""}</span>)}
          <span className={"pill" + (onlyNoted ? " on" : "")} onClick={() => setOnlyNoted((v) => !v)}>with notes</span>
          <span className={"pill" + (onlyEdited ? " on" : "")} onClick={() => setOnlyEdited((v) => !v)}>my edits</span>
          <button className="btn ghost sm" onClick={seed} disabled={busy}>{busy ? <><span className="spin" /> Rebuilding…</> : "↻ Rebuild From Account"}</button>
        </div>
        <div className="mut" style={{ fontSize: 12 }}>
          Each operator's <b>example</b> is copied into every generation, research and strategy prompt as the
          correct-usage reference (keyword arguments, bucket-as-group, densify-on-group, vector wrapping). Edit
          any example or note to steer the LLM — your edits are kept when you rebuild.
          {ops.length === 0 ? <> Log in and press <b>Rebuild From Account</b> to populate it.</> : null}
        </div>
        <div className="dx-head" style={{ marginTop: 10 }}><b>Sandbox</b><span className="mut">test an expression's operators / keyword args</span></div>
        <div className="dx-filters">
          <input value={sandbox} onChange={(e) => setSandbox(e.target.value)} placeholder="e.g. keep(close, open, period=5)"
            onKeyDown={(e) => e.key === "Enter" && runCheck()} style={{ flex: 1, fontFamily: "ui-monospace, monospace", fontSize: 12 }} />
          <button className="btn sm" onClick={runCheck} disabled={checking}>{checking ? <span className="spin" /> : "Validate"}</button>
        </div>
        {check ? <div style={{ fontSize: 12, marginTop: 6, color: check.ok ? "var(--ok)" : "var(--bad)" }}>
          {check.ok ? "✓ valid" : "✗ " + check.issues.map((i) => i.message || i.code).join("; ")}</div> : null}
      </div>

      <div className="panel panel-scroll" style={{ padding: 14 }}>
        {!view.length ? <div className="empty">No operators match — rebuild from your account after logging in, or clear the filter.</div> :
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 10 }}>
            {view.map((o) => {
              const u = use[o.name];
              const kw = o.params.filter((p) => p.kind === "keyword").map((p) => p.name);
              return (
                <div key={o.name} className="panel" style={{ padding: "10px 12px", boxShadow: "none", borderColor: o.user_edited ? "var(--acc)" : "var(--line)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <b style={{ color: "var(--acc)" }}>{o.name}</b>
                    <span className="mut" style={{ fontSize: 9, textTransform: "uppercase", letterSpacing: ".4px" }}>{o.scope}</span>
                    {o.user_edited ? <span className="badge ok">edited</span> : null}
                    {u ? <span className="mut" style={{ fontSize: 11 }}>used {u.count}×</span> : null}
                    <button className="btn ghost sm" style={{ marginLeft: "auto" }}
                      onClick={() => setEdit({ name: o.name, example: o.example, notes: o.notes })}>Edit</button>
                  </div>
                  {o.signature ? <div className="mut" style={{ fontSize: 11, marginTop: 3 }}>{o.signature}</div> : null}
                  {kw.length ? <div className="mut" style={{ fontSize: 11, marginTop: 2 }}>keyword args: {kw.join(", ")}</div> : null}

                  {edit?.name === o.name ?
                    <div style={{ marginTop: 6 }}>
                      <label className="fld"><span>Example</span>
                        <input value={edit.example} onChange={(e) => setEdit({ ...edit, example: e.target.value })} style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }} /></label>
                      <label className="fld" style={{ marginTop: 4 }}><span>Note</span>
                        <input value={edit.notes} onChange={(e) => setEdit({ ...edit, notes: e.target.value })} /></label>
                      <div className="dx-filters" style={{ marginTop: 6 }}>
                        <button className="btn sm" onClick={save}>Save</button>
                        <button className="btn ghost sm" onClick={() => setEdit(null)}>Cancel</button>
                      </div>
                    </div> :
                    <>
                      {o.example ? <code style={{ display: "block", background: "var(--surface-2)", padding: "5px 7px", borderRadius: 6, marginTop: 6, fontSize: 11.5 }}>{o.example}</code> : null}
                      {o.notes ? <div className="mut" style={{ fontSize: 11.5, marginTop: 4, fontStyle: "italic" }}>{o.notes}</div> : null}
                    </>}
                </div>
              );
            })}
          </div>}
      </div>
    </div>
  );
}
