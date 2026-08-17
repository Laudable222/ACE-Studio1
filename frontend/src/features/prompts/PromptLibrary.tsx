import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";

interface Prompt { id: number; name: string; scope: string; category: string; region: string; body: string; created_at: number; datasets: string[]; }

// The Prompt Library holds reusable prompts saved from the Research Lab and Strategy Atlas —
// the raw hypotheses/strategy with a nice name and an id. Open one in Generation, where you can
// auto-rewrite it into a full master prompt on demand.
export function PromptLibrary() {
  const { toast, toastErr } = useToast();
  const nav = useNavigate();
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [sel, setSel] = useState<Prompt | null>(null);
  const [q, setQ] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  async function exportAll() {
    const d = await api.get<any>("/generate/export");
    if (d.error) return toastErr(d.error);
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify(d, null, 2)], { type: "application/json" }));
    a.download = `ace_studio_export_${new Date().toISOString().slice(0, 10)}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    toast(`Exported ${d.prompts?.length || 0} prompt(s) + ${d.research_sessions?.length || 0} session(s).`, "ok");
  }
  async function importFile(f: File) {
    let payload: any;
    try { payload = JSON.parse(await f.text()); } catch { return toast("That isn't a valid export file.", "warn"); }
    const r = await api.post<any>("/generate/import", payload);
    if (r.error) return toastErr(r.error);
    toast(`Imported ${r.prompts_added} new prompt(s), ${r.sessions_added} session(s).`, "ok");
    load();
  }

  const load = () => api.get<{ prompts: Prompt[] }>("/generate/prompts").then((d) => {
    setPrompts(d.prompts || []);
    setSel((cur) => cur ? (d.prompts || []).find((p) => p.id === cur.id) || null : (d.prompts?.[0] || null));
  });
  useEffect(() => { load(); }, []);

  const view = useMemo(() => prompts.filter((p) =>
    !q || p.name.toLowerCase().includes(q.toLowerCase()) || (p.category || "").toLowerCase().includes(q.toLowerCase())), [prompts, q]);

  async function del(p: Prompt) {
    if (!confirm(`Delete prompt “${p.name}”?`)) return;
    const d = await api.post(`/generate/prompts/${p.id}/delete`);
    if (d.error) return toastErr(d.error);
    setSel(null); toast("Deleted."); load();
  }

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      <div className="panel" style={{ padding: 14, maxWidth: 340 }}>
        <div className="dx-head"><b>Prompts</b><span className="mut">{prompts.length}</span>
          <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={load}>Refresh</button>
          <button className="btn ghost sm" onClick={exportAll} title="Download all prompts + research sessions">Export</button>
          <button className="btn ghost sm" onClick={() => fileRef.current?.click()} title="Import a bundle">Import</button>
          <input ref={fileRef} type="file" accept="application/json" style={{ display: "none" }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) importFile(f); e.currentTarget.value = ""; }} /></div>
        <input placeholder="filter by name / category" value={q} onChange={(e) => setQ(e.target.value)} style={{ marginBottom: 8 }} />
        <div className="panel-scroll">
          {!view.length ? <div className="empty">No saved prompts yet. Run research or explore strategies, then “Push”.</div> :
            view.map((p) => (
              <div key={p.id} onClick={() => setSel(p)}
                style={{ padding: "8px 10px", borderRadius: 8, cursor: "pointer", marginBottom: 4,
                  background: sel?.id === p.id ? "var(--surface-2)" : "transparent", border: "1px solid " + (sel?.id === p.id ? "var(--line)" : "transparent") }}>
                <div style={{ fontWeight: 600, fontSize: 13 }}>{p.name} <span className="mut" style={{ fontSize: 11 }}>#{p.id}</span></div>
                <div className="mut" style={{ fontSize: 11 }}>
                  <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>{p.scope}</span>
                  {p.category ? " " + p.category : ""} {p.region ? "· " + p.region : ""}
                </div>
              </div>))}
        </div>
      </div>

      <div className="panel" style={{ padding: 14 }}>
        <div className="dx-head"><b>{sel ? sel.name : "Prompt"}</b>
          {sel ? <span className="mut">#{sel.id}</span> : null}
          {sel ? <>
            <button className="btn sm" style={{ marginLeft: "auto" }}
              onClick={() => { localStorage.setItem("ace2:gen:prompt", JSON.stringify(sel.body)); localStorage.setItem("ace2:gen:reqds", JSON.stringify(sel.datasets || [])); toast("Loaded into Generation."); nav("/generate"); }}>Open In Generation →</button>
            <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(sel.body); toast("Copied."); }}>Copy</button>
            <button className="btn ghost sm" style={{ color: "var(--bad)" }} onClick={() => del(sel)}>Delete</button>
          </> : null}
        </div>
        <div className="panel-scroll">
          {!sel ? <div className="empty">Select a prompt to view it.</div> :
            <pre style={{ whiteSpace: "pre-wrap", fontSize: 12.5, margin: 0, lineHeight: 1.6 }}>{sel.body}</pre>}
        </div>
      </div>
    </div>
  );
}
