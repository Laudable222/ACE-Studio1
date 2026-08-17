import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { useToast } from "../../lib/toast";

export function AlphaReplication() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();
  const [expression, setExpression] = useState("");
  const [sourceRegion, setSourceRegion] = useState("IND");
  const [sourceDelay, setSourceDelay] = useState(1);
  const [sourceUniverse, setSourceUniverse] = useState("TOP1000");
  const [targetRegion, setTargetRegion] = useState("GBR");
  const [targetDelay, setTargetDelay] = useState(1);
  const [targetUniverse, setTargetUniverse] = useState("TOP1000");
  const [mode, setMode] = useState("concept");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);

  async function preview() {
    if (!expression.trim()) return toast("Paste an alpha expression first.", "warn");
    setBusy(true);
    const d = await api.post<any>("/replication/preview", {
      expression, source_region: sourceRegion.toUpperCase(), source_delay: sourceDelay,
      source_universe: sourceUniverse, target_region: targetRegion.toUpperCase(),
      target_delay: targetDelay, target_universe: targetUniverse, mode,
    });
    setBusy(false);
    if (d.error) return toastErr(d.error);
    setResult(d);
    toast(`${d.candidates?.length || 0} verified target candidate(s) found.`, "ok");
  }

  function simulate(expr: string) {
    R.setPending([expr]);
    R.setCtx({ region: targetRegion.toUpperCase(), delay: targetDelay, universe: targetUniverse });
    nav("/simulate");
  }

  return <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
    <div className="panel panel-scroll" style={{ padding: 14 }}>
      <div className="dx-head"><b>Alpha Replication</b><span className="mut">successful alpha → target-region implementation</span></div>
      <p className="mut" style={{ fontSize: 12, lineHeight: 1.5 }}>
        Paste a proven alpha from one region. ACE decomposes its structure, identifies its datafields,
        checks the target region, and proposes exact or economically equivalent implementations. It never invents a BRAIN field.
      </p>
      <label className="fld"><span>Source alpha expression</span><textarea value={expression} onChange={e=>setExpression(e.target.value)} style={{minHeight:110,fontFamily:"monospace"}} placeholder="Paste a WorldQuant BRAIN alpha expression here…" /></label>
      <div style={{display:"grid",gridTemplateColumns:"1fr 90px 1fr",gap:8,marginTop:8}}>
        <label className="fld"><span>Source region</span><input value={sourceRegion} onChange={e=>setSourceRegion(e.target.value)} /></label>
        <label className="fld"><span>Delay</span><input type="number" min={0} max={1} value={sourceDelay} onChange={e=>setSourceDelay(Number(e.target.value))}/></label>
        <label className="fld"><span>Universe</span><input value={sourceUniverse} onChange={e=>setSourceUniverse(e.target.value)} /></label>
      </div>
      <div style={{display:"grid",gridTemplateColumns:"1fr 90px 1fr",gap:8,marginTop:8}}>
        <label className="fld"><span>Target region</span><input value={targetRegion} onChange={e=>setTargetRegion(e.target.value)} /></label>
        <label className="fld"><span>Delay</span><input type="number" min={0} max={1} value={targetDelay} onChange={e=>setTargetDelay(Number(e.target.value))}/></label>
        <label className="fld"><span>Universe</span><input value={targetUniverse} onChange={e=>setTargetUniverse(e.target.value)} /></label>
      </div>
      <label className="fld" style={{marginTop:8}}><span>Replication mode</span>
        <select value={mode} onChange={e=>setMode(e.target.value)}>
          <option value="exact">Exact field replication only</option>
          <option value="equivalent">Exact + equivalent field mapping</option>
          <option value="concept">Research-concept replication</option>
        </select>
      </label>
      <button className="btn" style={{marginTop:10,width:"100%"}} onClick={preview} disabled={busy}>{busy?<><span className="spin"/> Analysing portability…</>:"✦ Analyse & Replicate"}</button>
      <div className="mut" style={{fontSize:11,marginTop:8}}>Target fields are verified against BRAIN when a session is available. A candidate is not considered successful until simulated in the target region.</div>
    </div>

    <div className="panel panel-scroll" style={{padding:14}}>
      <div className="dx-head"><b>Replication report</b>{result&&<span className="mut">{result.source?.region} → {result.target?.region}</span>}</div>
      {!result ? <div className="empty">Paste a successful alpha and analyse it to see exact and concept-level target mappings.</div> : <>
        <div className="dx-filters wrap" style={{fontSize:11}}>
          <span className="badge">{result.source?.fields?.length||0} source field(s)</span>
          <span className="badge">{result.candidates?.length||0} candidate(s)</span>
          <span className={result.exact_possible?"badge ok":"badge"}>{result.exact_possible?"Exact replication available":"Adaptation required"}</span>
        </div>
        <h4>Field mapping</h4>
        {(result.field_maps||[]).map((m:any,i:number)=><div key={i} style={{padding:"8px 0",borderBottom:"1px solid var(--line)"}}>
          <code>{m.source?.id}</code><div className="mut" style={{fontSize:11}}>source: {m.source?.description||"No description available"}</div>
          {(m.candidates||[]).slice(0,5).map((c:any,j:number)=><div key={j} style={{marginTop:5,paddingLeft:8}}><code>{c.field?.id}</code> <span className="mut">{c.kind} · score {c.score}</span><div className="mut" style={{fontSize:10}}>{c.field?.description||""}</div></div>)}
          {!m.candidates?.length&&<div style={{color:"var(--bad)",fontSize:11,marginTop:5}}>No verified target equivalent found.</div>}
        </div>)}
        <h4>Candidate replications</h4>
        {(result.candidates||[]).map((c:any,i:number)=><div key={i} style={{padding:"9px 0",borderBottom:"1px solid var(--line)"}}>
          <div style={{display:"flex",gap:6,alignItems:"center"}}><span className="badge">#{i+1}</span><span className={c.valid?"badge ok":"badge bad"}>{c.valid?"validator passed":"validator failed"}</span>{c.llm_recommended&&<span className="badge" style={{background:"var(--acc-weak)",color:"var(--acc)"}}>research review</span>}</div>
          <code style={{display:"block",padding:"7px",background:"var(--surface-2)",borderRadius:6,marginTop:6}}>{c.expression}</code>
          <div className="mut" style={{fontSize:10,marginTop:4}}>mapping score {c.score} · {c.kind} · verified fields: {(c.verified_fields||[]).map((x:any)=>x.id).join(", ")}</div>
          {c.valid&&<div style={{display:"flex",gap:6,marginTop:7}}><button className="btn sm" onClick={()=>simulate(c.expression)}>Simulate in {targetRegion.toUpperCase()}</button><button className="btn ghost sm" onClick={()=>{navigator.clipboard?.writeText(c.expression);toast("Candidate copied.")}}>Copy</button></div>}
        </div>)}
        {result.llm_review?.rationale&&<><h4>Research review</h4><p className="mut" style={{fontSize:12}}>{result.llm_review.rationale}</p></>}
      </>}
    </div>
  </div>
}
