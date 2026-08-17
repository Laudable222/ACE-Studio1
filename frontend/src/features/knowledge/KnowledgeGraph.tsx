import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { useToast } from "../../lib/toast";
import "../data/data.css";

interface Overview {
  datasets: number; fields: number; regions: string[];
  cross_region_concepts: number; categories: { category: string; count: number }[];
}
interface Twin {
  dataset_id: string; region: string; delay: number; name: string; category: string;
  exact: boolean; score: number; ai_related?: boolean; ai_reason?: string;
}
interface Sim { dataset_id: string; name: string; category?: string; score: number; shared?: string[]; ai_related?: boolean; ai_reason?: string; }

// Category → pillar → colour (matches the backend three-pillar map / the BRAIN guide).
const PILLAR: Record<string, string> = {
  fundamental: "iv", earnings: "iv", analyst: "exp", sentiment: "exp", macro: "exp",
  option: "pos", short_interest: "pos", shortinterest: "pos", institutions: "pos", insiders: "pos",
  news: "ctx", social_media: "ctx", socialmedia: "ctx",
};
const PILLAR_COLOR: Record<string, string> = { iv: "#34d399", exp: "#60a5fa", pos: "#a78bfa", ctx: "#fb923c", other: "#94a3b8" };
const pillarColor = (cat?: string) => PILLAR_COLOR[PILLAR[String(cat || "").toLowerCase().replace(/[\s-]/g, "_")] || "other"];

// The Knowledge Graph surfaces what the DB has learned across every fetch. Relationships are
// found in two stages: (1) a fast lexical pass (token cosine over names/descriptions) proposes
// candidates, and (2) an optional AI verification prunes coincidental word-matches so only
// genuinely related concepts remain. "Twins" are the SAME concept found in OTHER regions — the
// basis of cross-region research.
export function KnowledgeGraph() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();
  const [ov, setOv] = useState<Overview | null>(null);
  const [pick, setPick] = usePersistentState("kg:pick", "");
  const [twins, setTwins] = usePersistentState<Twin[] | null>("kg:twins", null);
  const [similar, setSimilar] = usePersistentState<Sim[]>("kg:similar", []);
  const [name, setName] = usePersistentState("kg:name", "");
  const [aiOnly, setAiOnly] = usePersistentState("kg:aionly", false);
  const [xr, setXr] = usePersistentState<any | null>("kg:xr", null);   // AI cross-region analysis
  const [xrBusy, setXrBusy] = useState(false);
  const [busy, setBusy] = useState("");
  const [memories,setMemories]=useState<any[]>([]),[memoryType,setMemoryType]=useState("tip"),[memoryTitle,setMemoryTitle]=useState(""),[memoryContent,setMemoryContent]=useState(""),[memoryRegion,setMemoryRegion]=useState(""),[memoryConfidence,setMemoryConfidence]=useState("unverified");   // "" | "twins" | "similar"

  useEffect(() => { api.get<Overview>("/knowledge/overview").then(setOv); api.get<any>("/knowledge/memory?limit=20").then(d=>setMemories(d.items||[])); }, []);
  async function saveMemory(){if(!memoryContent.trim())return toast("Paste a tip, rule, observation or lesson first.","warn");const d=await api.post<any>("/knowledge/memory",{type:memoryType,title:memoryTitle,content:memoryContent,region:memoryRegion,confidence:memoryConfidence,source:"user"});if(d.error)return toastErr(d.error);setMemories([d,...memories]);setMemoryTitle("");setMemoryContent("");toast("Saved to Knowledge Vault.","ok");}

  async function lookup(id: string) {
    setPick(id);
    if (!id) { setTwins(null); return; }
    const t = await api.get<{ twins: Twin[]; name: string }>(`/knowledge/dataset/${id}/twins?region=${R.ctx.region}&delay=${R.ctx.delay}`);
    setTwins(t.twins || []); setName(t.name || id);
    const s = await api.get<{ similar: Sim[] }>(`/knowledge/dataset/${id}/similar?region=${R.ctx.region}&delay=${R.ctx.delay}`);
    setSimilar(s.similar || []);
  }

  async function verify(kind: "twins" | "similar") {
    const cands = (kind === "twins" ? (twins || []) : similar).map((c: any) => ({
      dataset_id: c.dataset_id, name: c.name, description: "", category: c.category || "",
      region: c.region || R.ctx.region,
    }));
    if (!cands.length) return;
    setBusy(kind);
    const start = await api.post<any>("/knowledge/judge", { base_id: pick, region: R.ctx.region, delay: R.ctx.delay, candidates: cands });
    if (start.error || !start.job_id) { setBusy(""); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/knowledge/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setBusy("");
    if (s.status !== "done") return toastErr(s.error || s.status);
    const judged = s.result?.judged || [];
    const byId: Record<string, any> = {}; judged.forEach((j: any) => { byId[j.dataset_id] = j; });
    if (kind === "twins") setTwins((twins || []).map((t) => ({ ...t, ...byId[t.dataset_id] })));
    else setSimilar(similar.map((t) => ({ ...t, ...byId[t.dataset_id] })));
    const kept = judged.filter((j: any) => j.ai_related).length;
    toast(`AI confirmed ${kept} of ${judged.length} relationships make sense.`, "ok");
  }

  const quick = R.selDatasets.slice(0, 8);
  const regionsFetched = ov?.regions?.length ?? 0;
  const enoughRegions = regionsFetched >= 2;   // cross-region twins need ≥2 regions fetched
  const showTwins = (twins || []).filter((t) => !aiOnly || t.ai_related !== false);
  const showSim = similar.filter((t) => !aiOnly || t.ai_related !== false);

  async function analyzeCrossRegion() {
    setXrBusy(true);
    const start = await api.post<any>("/knowledge/cross-region", {});
    if (start.error || !start.job_id) { setXrBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/knowledge/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setXrBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    setXr(s.result);
    const n = (s.result?.concepts || []).length;
    toast(s.result?.enough_regions ? `Analysed ${n} cross-region concept(s).` : "Need datasets in ≥2 regions first.", n ? "ok" : "warn");
  }

  function researchHere(region: string, delay: number) {
    R.setCtx({ region, delay });
    toast(`Working region set to ${region} · D${delay}. Fetch its datasets in the Data Explorer, then research.`);
    nav("/data");
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, flex: 1, minHeight: 0 }}>
      <div className="panel" style={{padding:14}}><div className="dx-head"><b>Knowledge Vault</b><span className="mut">Stored locally. Retrieved when relevant. Not model training.</span></div><div style={{display:"grid",gridTemplateColumns:"130px 1fr 110px",gap:8,marginTop:9}}><select value={memoryType} onChange={e=>setMemoryType(e.target.value)}><option value="rule">Hard Rule</option><option value="tip">Research Tip</option><option value="observation">Observation</option><option value="evidence">Simulation Evidence</option><option value="lesson">Simulation Lesson</option></select><input value={memoryTitle} onChange={e=>setMemoryTitle(e.target.value)} placeholder="Short title (optional)"/><input value={memoryRegion} onChange={e=>setMemoryRegion(e.target.value.toUpperCase())} placeholder="Region"/></div><textarea value={memoryContent} onChange={e=>setMemoryContent(e.target.value)} placeholder="Paste a tip, rule, observation, or lesson here…" style={{width:"100%",minHeight:70,marginTop:8}}/><div style={{display:"flex",gap:8,alignItems:"center",marginTop:7}}><select value={memoryConfidence} onChange={e=>setMemoryConfidence(e.target.value)}><option value="unverified">Unverified</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select><button className="btn sm" onClick={saveMemory}>Save to Vault</button><span className="mut" style={{fontSize:11}}>{memories.length} recent memories</span></div>{memories.slice(0,5).map(m=><div key={m.id} style={{marginTop:8,padding:8,background:"var(--surface-2)",borderRadius:7}}><b>{m.title}</b> <span className="badge">{m.type}</span>{m.region&&<span className="mut" style={{marginLeft:6}}>{m.region}</span>}<div className="mut" style={{fontSize:11,marginTop:3}}>{m.content}</div></div>)}</div>

      {/* stats */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: 12 }}>
        {[
          { l: "Datasets Known", v: ov?.datasets ?? "—" },
          { l: "Fields Known", v: ov?.fields ?? "—" },
          { l: "Regions", v: ov?.regions?.length ?? "—" },
          { l: "Cross-Region Concepts", v: ov?.cross_region_concepts ?? "—" },
        ].map((k) => (
          <div key={k.l} className="panel" style={{ padding: 14 }}>
            <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px" }}>{k.l}</div>
            <div style={{ fontSize: 26, fontWeight: 700, color: "var(--acc)", marginTop: 4 }}>{k.v}</div>
          </div>
        ))}
      </div>

      {/* visual relationship graph — base dataset at centre, twins/related on a ring, coloured by pillar */}
      {(twins && twins.length) || showSim.length ? (() => {
        const nodes = [
          ...showTwins.slice(0, 8).map((t) => ({ id: t.dataset_id, cat: t.category, sub: t.region })),
          ...showSim.slice(0, 6).map((s) => ({ id: s.dataset_id, cat: s.category, sub: "same region" })),
        ];
        const cx = 320, cy = 110, R2 = 90;
        return (
          <div className="panel" style={{ padding: 12 }}>
            <div className="dx-head"><b>Relationship Graph · {name}</b>
              <span className="mut" style={{ fontSize: 11 }}>
                {(["iv", "exp", "pos", "ctx"] as const).map((p) => <span key={p} style={{ marginLeft: 10 }}><span style={{ display: "inline-block", width: 8, height: 8, borderRadius: 8, background: PILLAR_COLOR[p], marginRight: 4 }} />{{ iv: "Intrinsic", exp: "Expectations", pos: "Positioning", ctx: "Context" }[p]}</span>)}
              </span></div>
            <svg viewBox="0 0 640 220" width="100%" height="220" style={{ background: "var(--surface-2)", borderRadius: 8 }}>
              {nodes.map((n, i) => {
                const a = (i / Math.max(1, nodes.length)) * 2 * Math.PI - Math.PI / 2;
                const x = cx + R2 * Math.cos(a), y = cy + R2 * Math.sin(a);
                return <g key={i}>
                  <line x1={cx} y1={cy} x2={x} y2={y} stroke="var(--line)" strokeWidth="1" />
                  <circle cx={x} cy={y} r="7" fill={pillarColor(n.cat)} />
                  <text x={x} y={y - 10} fontSize="9" fill="var(--mut)" textAnchor="middle">{n.id}</text>
                </g>;
              })}
              <circle cx={cx} cy={cy} r="11" fill="var(--acc)" />
              <text x={cx} y={cy + 24} fontSize="10" fill="var(--fg)" textAnchor="middle">{pick || name}</text>
            </svg>
          </div>
        );
      })() : null}

      <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
        {/* twins */}
        <div className="panel dx-panel">
          <div className="dx-head"><b>Cross-Region Concepts</b>
            <span className="mut">The same concept across regions</span>
            <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={analyzeCrossRegion} disabled={xrBusy || !enoughRegions}
              title={enoughRegions ? "Let AI parse all fetched datasets and rate the best cross-region opportunities" : "Fetch datasets in ≥2 regions first"}>
              {xrBusy ? <><span className="spin" /> Analysing…</> : "✦ Analyze Cross-Region (AI)"}</button>
          </div>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <input placeholder="Dataset id (e.g. news18)" value={pick}
              onChange={(e) => setPick(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && lookup(pick.trim())} style={{ flex: 1 }} />
            <button className="btn sm" onClick={() => lookup(pick.trim())}>Find Twins</button>
          </div>
          {quick.length > 0 &&
            <div className="dx-filters wrap" style={{ marginBottom: 8 }}>
              <span className="mut" style={{ fontSize: 11 }}>Selected:</span>
              {quick.map((d) => <span key={d} className="pill" onClick={() => lookup(d)}>{d}</span>)}
              {(twins || []).some((t) => t.ai_related !== undefined) ?
                <span className={"pill" + (aiOnly ? " on" : "")} onClick={() => setAiOnly((v) => !v)}>AI-approved only</span> : null}
            </div>}
          <div className="panel-scroll">
            {xr?.concepts?.length ? (
              <div style={{ marginBottom: 12 }}>
                <div className="dx-head"><b>AI Cross-Region Opportunities</b>{xr.provider ? <span className="mut">via {xr.provider}</span> : null}</div>
                <table><thead><tr><th>Concept</th><th>Regions</th><th>Rating</th><th>Research</th></tr></thead>
                  <tbody>{xr.concepts.slice(0, 30).map((c: any, i: number) => (
                    <tr key={i}>
                      <td><code>{c.dataset_id}</code>{c.category ? <span className="cat-tag">{c.category}</span> : null}
                        <div className="mut sub">{(c.name || "").slice(0, 34)}</div>
                        {c.reason ? <div className="mut sub" style={{ fontStyle: "italic" }}>{c.reason}</div> : null}</td>
                      <td className="mut" style={{ fontSize: 11 }}>{(c.regions || []).join(", ")}</td>
                      <td><span className="badge" style={{ background: c.rating === "high" ? "var(--ok-weak)" : c.rating === "low" ? "var(--bad-weak)" : "var(--faint)", color: c.rating === "high" ? "var(--ok)" : c.rating === "low" ? "var(--bad)" : "var(--mut)" }}>{c.rating || "—"}</span></td>
                      <td>{(c.regions || []).map((rg: string) => <button key={rg} className="btn ghost sm" style={{ marginRight: 4, marginBottom: 3 }} onClick={() => researchHere(rg, R.ctx.delay)}>{rg} →</button>)}</td>
                    </tr>))}</tbody></table>
              </div>
            ) : null}
            {xr && !xr.enough_regions ? <div className="empty">Cross-region analysis needs datasets in at least 2 regions — you have {xr.regions_known}. Fetch datasets in another region first.</div> : null}
            {!enoughRegions ? <div className="empty">Cross-region concepts need datasets fetched in at least 2 regions — you currently have {regionsFetched}. Fetch datasets in another region (Data Explorer) first.</div> :
              twins === null ? <div className="empty">Enter a dataset id (fetch datasets in the Data Explorer first, in two or more regions).</div> :
              !showTwins.length ? <div className="empty">No same-category twins for “{name}” in your other fetched regions yet.</div> :
                <table><thead><tr><th>Region</th><th>Dataset</th><th>Match</th><th></th></tr></thead>
                  <tbody>{showTwins.map((t, i) => (
                    <tr key={i}><td>{t.region}</td>
                      <td><code>{t.dataset_id}</code><div className="mut sub">{(t.name || "").slice(0, 36)}</div>
                        {t.ai_reason ? <div className="mut sub" style={{ fontStyle: "italic" }}>{t.ai_reason}</div> : null}</td>
                      <td>{t.exact ? <span className="badge ok">exact</span> : <span className="mut">{t.score}</span>}
                        {t.ai_related === true ? <span className="badge ok" style={{ marginLeft: 4 }}>AI ✓</span> :
                          t.ai_related === false ? <span className="badge bad" style={{ marginLeft: 4 }}>AI ✗</span> : null}</td>
                      <td><button className="btn ghost sm" onClick={() => researchHere(t.region, t.delay)}>Research Here →</button></td></tr>))}
                  </tbody></table>}
          </div>
        </div>

        {/* similar + explainer */}
        <div className="panel dx-panel">
          <div className="dx-head"><b>Related In This Region</b>
            <span className="mut">Spread research across similar data</span>
            {similar.length ? <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={() => verify("similar")} disabled={!!busy}>
              {busy === "similar" ? <><span className="spin" /> Verifying…</> : "✦ Verify With AI"}</button> : null}
          </div>
          <div className="panel-scroll">
            {!showSim.length ? <div className="empty">Pick a dataset on the left to see related datasets in {R.ctx.region}.</div> :
              <table><thead><tr><th>Dataset</th><th>Category</th><th>Match</th></tr></thead>
                <tbody>{showSim.map((s, i) => (
                  <tr key={i} title={s.shared?.length ? `Matched on shared words: ${s.shared.join(", ")}` : ""}>
                    <td><code>{s.dataset_id}</code><div className="mut sub">{(s.name || "").slice(0, 34)}</div>
                    {s.shared?.length ? <div className="mut sub" style={{ fontSize: 10.5 }}>↳ {s.shared.join(" · ")}</div> : null}
                    {s.ai_reason ? <div className="mut sub" style={{ fontStyle: "italic" }}>{s.ai_reason}</div> : null}</td>
                    <td className="mut">{s.category || "—"}</td>
                    <td className="mut">{s.score}{s.ai_related === true ? <span className="badge ok" style={{ marginLeft: 4 }}>AI ✓</span> : s.ai_related === false ? <span className="badge bad" style={{ marginLeft: 4 }}>AI ✗</span> : null}</td></tr>))}
                </tbody></table>}

            <div className="dx-head" style={{ marginTop: 12 }}><b>How This Works</b></div>
            <div className="mut" style={{ fontSize: 12, lineHeight: 1.7 }}>
              Related datasets are found by <b>word overlap</b> between this dataset and the others in your
              region — a token cosine over their names and descriptions. The <b>name is weighted ~3× the
              description</b>, because names are the curated high-signal label while descriptions share a lot of
              generic finance vocabulary. Each row shows the <b>shared words</b> that drove the match (hover for
              the full list), so a score is never opaque. <b>Verify With AI</b> then confirms each is genuinely
              the same or a substitutable concept (with a reason), dropping coincidental overlaps.
              <br /><br />
              Note: this list is intentionally broad (any category) to help you <i>spread</i> research across
              related-but-different data. For the same concept in another region, use Twins below — those are
              exact-id matches, or same-category only.
              <br /><br />
              <b>Cross-region:</b> a “twin” is the same concept catalogued in another region — this needs
              datasets fetched in at least two regions. Use <b>Research Here</b> to switch your working region
              to a twin's region, then research the same idea there (or build it in Super Alpha) to test faster
              and diversify across markets.
            </div>

            {ov?.categories?.length ? <>
              <div className="dx-head" style={{ marginTop: 12 }}><b>Categories Learned</b></div>
              <div className="dx-filters wrap">
                {ov.categories.slice(0, 18).map((c) =>
                  <span key={c.category} className="pill">{c.category} <span className="mut">{c.count}</span></span>)}
              </div>
            </> : null}
          </div>
        </div>
      </div>
    </div>
  );
}
