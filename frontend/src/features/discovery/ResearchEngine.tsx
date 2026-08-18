import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { useToast } from "../../lib/toast";
import "../templates/templates.css";

type Doc = { id:number; title:string; source:string; status:string; chars:number; extraction:any };

export function ResearchEngine() {
  const R=useResearch(); const {toast,toastErr}=useToast(); const fileRef=useRef<HTMLInputElement>(null);
  const nav=useNavigate();
  const [docs,setDocs]=useState<Doc[]>([]); const [selected,setSelected]=useState<Doc|null>(null);
  const [busy,setBusy]=useState(false); const [analysis,setAnalysis]=useState<any>(null); const [matches,setMatches]=useState<any[]>([]);
  const [experiments,setExperiments]=useState<any[]>([]); const [name,setName]=useState("");
  const [expanded,setExpanded]=useState<Set<number>>(new Set()); const [bulkMsg,setBulkMsg]=useState("");

  const refresh=()=>api.get<any>("/discovery/documents").then(d=>setDocs(d.documents||[]));
  useEffect(()=>{ refresh(); api.get<any>("/discovery/experiments").then(d=>setExperiments(d.experiments||[])); },[]);

  async function upload(file:File){
    const fd=new FormData(); fd.append("file",file); setBusy(true);
    const d=await api.upload<any>("/discovery/documents",fd); setBusy(false);
    if(d.error) return toastErr(d.error); await refresh();
    const doc=(await api.get<any>("/discovery/documents")).documents?.find((x:any)=>x.id===d.id); if(doc) setSelected(doc);
    toast(d.duplicate?"That report is already in the library.":"Research report added.","ok");
  }
  async function analyze(){
    if(!selected) return toast("Select a research report first.","warn");
    setBusy(true); const start=await api.post<any>("/discovery/analyze",{document_id:selected.id,use_llm:true,auto_map:true,region:R.ctx.region||"IND",delay:R.ctx.delay||1,universe:R.ctx.universe||"TOP3000",instrument:"EQUITY"});
    if(start.error){setBusy(false);return toastErr(start.error)}
    let s:any={}; for(;;){s=await api.get(`/research/jobs/${start.job_id}`); if(s.status!=="running") break; await new Promise(r=>setTimeout(r,1000));}
    setBusy(false); if(s.status!=="done") return toastErr(s.error||"Analysis failed.");
    setAnalysis(s.result?.extraction||{}); setMatches(s.result?.field_mapping?.matches||[]); toast(`Research map created. ${s.result?.field_mapping?.matches?.length||0} BRAIN fields mapped automatically.`,"ok"); await refresh();
  }
  async function map(){
    if(!analysis) return;
    setBusy(true);
    const d=await api.post<any>("/discovery/map-fields-auto",{
      extraction:analysis, top_k:16, region:R.ctx.region||"IND", delay:R.ctx.delay||1,
      universe:R.ctx.universe||"TOP3000", instrument:"EQUITY"
    });
    setBusy(false);
    if(d.error) return toastErr(d.error);
    setMatches(d.matches||[]);
    toast(`Mapped ${d.matches?.length||0} verified BRAIN fields across ${d.datasets_scanned||0} datasets.`,"ok");
  }
  async function createExperiment(h:any){
    const scoped=matches.filter(m=>{ const x=m.hypothesis; return !x || x===h || (x.statement && h.statement && x.statement===h.statement); });
    const seen=new Set<string>(); const picked:any[]=[];
    for(const m of scoped){ const f=m.field; if(!f?.id||!f?.dataset_id) continue; const k=`${f.id}|${f.dataset_id}`; if(seen.has(k)) continue; seen.add(k); picked.push(f); if(picked.length>=8) break; }
    if(!picked.length) return toast("Map this hypothesis to BRAIN fields first.","warn");
    const d=await api.post<any>("/discovery/experiments",{name:name||h.statement?.slice(0,70)||"Research experiment",region:R.ctx.region,delay:R.ctx.delay,universe:R.ctx.universe,research_ids:selected?[selected.id]:[],hypothesis:h,field_ids:picked});
    if(d.error) return toastErr(d.error); setName(""); const e=await api.get<any>("/discovery/experiments"); setExperiments(e.experiments||[]); toast(`Experiment #${d.id} created.`,"ok");
  }
  async function generateOne(e:any):Promise<number>{
    // -1 signals skip/failure; caller decides how to report it. Shared by the single-experiment
    // and "generate for all hypotheses" paths so both run the exact same job + poll logic.
    if(!e.field_ids?.length) return -1;
    const start=await api.post<any>("/discovery/experiments/generate",{experiment_id:e.id,n:12,max_operators:4,repair_rounds:2});
    if(start.error) return -1;
    let s:any={}; for(;;){s=await api.get(`/research/jobs/${start.job_id}`); if(s.status!=="running") break; await new Promise(r=>setTimeout(r,1000));}
    if(s.status!=="done") return -1;
    return s.result?.valid?.length||0;
  }
  async function generateExperiment(e:any){
    if(!e.field_ids?.length) return toast("This experiment has no saved BRAIN fields — map the hypothesis and create it again.","warn");
    setBusy(true);
    const n=await generateOne(e);
    setBusy(false);
    if(n<0) return toastErr("Generation failed.");
    const d=await api.get<any>("/discovery/experiments"); setExperiments(d.experiments||[]); toast(`${n} candidates added to experiment #${e.id}.`,"ok");
  }
  async function generateAll(){
    const targets=experiments.filter(e=>e.field_ids?.length);
    if(!targets.length) return toast("No experiments have BRAIN fields mapped yet.","warn");
    setBusy(true); let total=0, failed=0;
    for(let i=0;i<targets.length;i++){
      setBulkMsg(`Generating hypothesis ${i+1}/${targets.length}: ${targets[i].name||"#"+targets[i].id}…`);
      const n=await generateOne(targets[i]);
      if(n<0) failed++; else total+=n;
    }
    setBulkMsg(""); setBusy(false);
    const d=await api.get<any>("/discovery/experiments"); setExperiments(d.experiments||[]);
    toast(`${total} candidate(s) added across ${targets.length-failed}/${targets.length} hypothes${targets.length===1?"is":"es"}.${failed?` ${failed} failed.`:""}`,failed?"warn":"ok");
  }
  function toggleExpand(id:number){ setExpanded(prev=>{ const n=new Set(prev); if(n.has(id)) n.delete(id); else n.add(id); return n; }); }
  function sendExperimentToSimulate(e:any){
    const exprs:string[]=e.expressions||[];
    if(!exprs.length) return toast("This experiment has no generated candidates yet — Generate first.","warn");
    // An experiment carries its OWN region/delay/universe, which can differ from whatever the
    // rest of the app is currently pointed at (Data Explorer etc). Simulation always simulates
    // under that shared global context — not from anything handed to it — so without syncing
    // it first, these expressions would silently run under the wrong region.
    const drift = e.region!==R.ctx.region || e.delay!==R.ctx.delay || e.universe!==R.ctx.universe;
    R.setCtx({instrument:"EQUITY", region:e.region, delay:e.delay, universe:e.universe});
    R.setPending(exprs);
    if(drift) toast(`Switched context to ${e.region} D${e.delay} ${e.universe} to match this experiment.`,"ok");
    nav("/simulate");
  }

  function sendToGeneration(h:any){
    const matched=matches.slice(0,8).map(x=>x.field.id).join(", ");
    const text=`Research hypothesis:\n${h.statement||h.idea||""}\n\nMechanism: ${h.mechanism||""}\nExpected sign: ${h.expected_sign||h.sign||""}\nHorizon: ${h.horizon||""}\n\nCandidate BRAIN fields from the research map: ${matched}\n\nConstruct genuinely different expressions that test this mechanism. Treat the hypothesis as the research target, not as a formula to copy.`;
    navigator.clipboard?.writeText(text); toast("Research brief copied. Open Generation and paste it into the instruction box.","ok");
  }

  return <div className="dx-split" style={{flex:1,minHeight:0}}>
    <div className="panel" style={{padding:14,overflow:"auto"}}>
      <div className="dx-head"><b>Research Engine</b><span className="mut">paper → hypothesis → fields → experiment</span></div>
      <p className="mut" style={{fontSize:12,lineHeight:1.5}}>Feed ACE Markdown research reports. It extracts a broad research map, generates testable hypotheses, then scans the BRAIN catalogue and selects verified fields automatically. Manual Data Explorer selection is optional.</p>
      <input ref={fileRef} type="file" accept=".md,.markdown,.txt" hidden onChange={e=>e.target.files?.[0]&&upload(e.target.files[0])}/>
      <button className="btn" onClick={()=>fileRef.current?.click()} disabled={busy}>{busy?<><span className="spin"/> Working…</>:"＋ Add Markdown report"}</button>
      <div className="panel-scroll" style={{marginTop:10,maxHeight:260}}>
        {docs.map(d=><div key={d.id} onClick={()=>{setSelected(d);setAnalysis(d.extraction?.hypotheses?d.extraction:null);setMatches([])}} style={{padding:"9px 6px",borderBottom:"1px solid var(--line)",cursor:"pointer",background:selected?.id===d.id?"var(--faint)":"transparent"}}><b>{d.title}</b><div className="mut" style={{fontSize:11}}>{d.status} · {d.chars.toLocaleString()} chars</div></div>)}
        {!docs.length&&<div className="empty">No research reports yet.</div>}
      </div>
      {selected&&<button className="btn ghost sm" style={{marginTop:8}} onClick={analyze} disabled={busy}>✦ Analyse selected report</button>}
    </div>
    <div className="panel" style={{padding:14,overflow:"auto"}}>
      <div className="dx-head"><b>{analysis?.title||"Research map"}</b>{analysis&&<button className="btn ghost sm" style={{marginLeft:"auto"}} onClick={map}>Auto-map BRAIN fields</button>}</div>
      {!analysis?<div className="empty">Analyse a report to see its findings and hypotheses.</div>:<>
        <div className="dx-filters wrap" style={{fontSize:11}}><span className="badge">{analysis.findings?.length||0} findings</span><span className="badge">{analysis.mechanisms?.length||0} mechanisms</span><span className="badge">{analysis.variables?.length||0} variables</span><span className="badge">{analysis.hypotheses?.length||0} hypotheses</span>{analysis.provenance?.provider&&<span className="badge">LLM: {analysis.provenance.provider}/{analysis.provenance.model||""}</span>}{analysis.analysis_mode&&<span className={analysis.analysis_mode==="LLM_RESEARCH_ANALYSIS"?"badge":"badge bad"}>{analysis.analysis_mode==="LLM_RESEARCH_ANALYSIS"?"structured LLM":"heuristic fallback"}</span>}{analysis.analysis_note&&<span className="badge">{analysis.analysis_note}</span>}{analysis.analysis_error&&<span className="badge bad">fallback: {analysis.analysis_error}</span>}</div>
        <h4>Research question</h4><p className="mut">{analysis.research_question||"Not stated explicitly in the report."}</p>
        <h4>Findings</h4>{(analysis.findings||[]).slice(0,12).map((f:any,i:number)=><div key={i} style={{padding:"7px 0",borderBottom:"1px solid var(--line)",fontSize:12}}><b>{f.text||f.statement||f}</b><div className="mut" style={{fontSize:10}}>{f.evidence||"SOURCE_SUPPORTED"}{f.source_lines?.length?` · lines ${f.source_lines.join(", ")}`:""}</div></div>)}<h4>Hypotheses</h4>
        {(analysis.hypotheses||[]).map((h:any,i:number)=><div key={i} style={{padding:"10px 0",borderBottom:"1px solid var(--line)"}}><b>{h.statement||h.idea}</b><div className="mut" style={{fontSize:11,marginTop:4}}>{h.mechanism||""} · sign {h.expected_sign||h.sign||"?"} · horizon {h.horizon||"?"}</div><div style={{display:"flex",gap:6,marginTop:7}}><button className="btn sm" onClick={()=>createExperiment(h)}>Create experiment</button><button className="btn ghost sm" onClick={()=>sendToGeneration(h)}>Copy generation brief</button></div></div>)}
        {matches.length>0&&<><h4>Best field matches</h4>{matches.map((m:any,i:number)=><div key={i} style={{padding:"5px 0",fontSize:12}}><code>{m.field.id}</code> <span className="mut">score {m.score} · {m.matched_terms?.join(", ")}</span></div>)}</>}
      </>}
    </div>
    <div className="panel" style={{padding:14,overflow:"auto"}}>
      <div className="dx-head"><b>Experiments</b><span className="mut">{experiments.length}</span></div>
      <label className="fld"><span>Next experiment name</span><input value={name} onChange={e=>setName(e.target.value)} placeholder="e.g. Cash-flow underreaction"/></label>
      {experiments.length>1&&<button className="btn ghost sm" style={{marginTop:6}} onClick={generateAll} disabled={busy}>{busy&&bulkMsg?<><span className="spin"/> {bulkMsg}</>:"Generate for all hypotheses"}</button>}
      {experiments.map(e=><div key={e.id} style={{padding:"9px 0",borderBottom:"1px solid var(--line)"}}>
        <b>#{e.id} {e.name}</b>
        <div className="mut" style={{fontSize:11}}>{e.status} · {e.region} D{e.delay} · <span style={{cursor:e.field_ids?.length?"pointer":"default",textDecoration:e.field_ids?.length?"underline":"none"}} onClick={()=>e.field_ids?.length&&toggleExpand(e.id)}>{expanded.has(e.id)?"▾":"▸"} {e.field_ids?.length||0} field{e.field_ids?.length===1?"":"s"}</span> · {e.expressions?.length||0} candidates</div>
        {expanded.has(e.id)&&<div style={{marginTop:5,marginBottom:2,fontSize:11}}>{(e.field_ids||[]).map((f:any,i:number)=><div key={i} className="mut" style={{padding:"2px 0"}}><code>{typeof f==="string"?f:f.id}</code>{typeof f!=="string"&&f.dataset_id&&<> — {f.dataset_id}</>}</div>)}</div>}
        <div style={{fontSize:12,marginTop:4}}>{e.hypothesis?.statement||""}</div>
        <div style={{display:"flex",gap:6,marginTop:6}}>
          <button className="btn ghost sm" onClick={()=>generateExperiment(e)} disabled={busy}>Generate candidates</button>
          {e.expressions?.length>0&&<button className="btn sm" onClick={()=>sendExperimentToSimulate(e)}>Send {e.expressions.length} To Simulate →</button>}
        </div>
      </div>)}
      {!experiments.length&&<div className="empty">Create an experiment from a hypothesis.</div>}
    </div>
  </div>
}
