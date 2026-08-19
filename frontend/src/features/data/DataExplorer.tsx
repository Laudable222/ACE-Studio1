import {useEffect,useMemo,useState} from "react";
import {api} from "../../lib/api";
import {useResearch, type Field} from "../../lib/store";
import {useToast} from "../../lib/toast";
import "./data.css";

export function DataExplorer(){
 const R=useResearch(),{toastErr}=useToast();
 const [datasets,setDatasets]=useState<any[]>(R.datasets||[]),[fields,setFields]=useState<any[]>(R.fields||[]);
 const [status,setStatus]=useState<any>(null),[loading,setLoading]=useState(false),[loadingMsg,setLoadingMsg]=useState(""),[search,setSearch]=useState("");
 const [selected,setSelected]=useState<string[]>(R.selDatasets||[]);
 const [region,setRegion]=useState(R.ctx.region||"IND"),[delay,setDelay]=useState(R.ctx.delay||1),[universe,setUniverse]=useState(R.ctx.universe||"TOP1000");
 useEffect(()=>{api.get<any>("/data/status").then(setStatus)},[]);
 useEffect(()=>{setDatasets(R.datasets||[]);setFields(R.fields||[]);setSelected(R.selDatasets||[])},[R.datasets,R.fields,R.selDatasets]);
 const sync=(ds:any[],fs:any[],sel:string[],ctxPatch:any={})=>{
  R.setDatasets(ds);
  R.setFields(fs);
  R.setSelDatasets(sel);
  R.setSelFields(fs.map(f=>f.id));
  R.setCtx(ctxPatch);
 };
 async function fetchDs(){
  setLoading(true); setLoadingMsg("fetching datasets…");
  const start=await api.post<any>("/data/datasets",{region,delay,universe,instrument:"EQUITY"});
  if(start.error){setLoading(false);return toastErr(start.error)}
  // The backend now fetches datasets AND catalogues all of their fields in one background
  // job (can take a few minutes for a large universe), so the local DB is complete and
  // Research Engine's Auto-map never has to fall back to a manual BRAIN fetch.
  let s:any={};
  for(;;){
   s=await api.get<any>(`/research/jobs/${start.job_id}`);
   if(s.message) setLoadingMsg(s.message);
   if(s.status!=="running") break;
   await new Promise(r=>setTimeout(r,1000));
  }
  setLoading(false);
  if(s.status!=="done") return toastErr(s.error||"Fetching datasets failed.");
  const d=s.result||{};
  const effective={
   region:d.effective?.region??region,
   delay:d.effective?.delay??delay,
   universe:d.effective?.universe??universe,
  };
  const rows=(d.rows||[]).map((x:any)=>({...x,...effective}));
  setRegion(effective.region);setDelay(effective.delay);setUniverse(effective.universe);
  setDatasets(rows);setFields([]);setSelected([]);
  sync(rows,[],[],effective);
 }
 async function fetchFields(ids:string[]){
  if(!ids.length){setFields([]);sync(datasets,[],[],{region,delay,universe});return}
  const d=await api.post<any>("/data/fields",{dataset_ids:ids,region,delay,universe,instrument:"EQUITY"});
  if(d.error)return toastErr(d.error);
  const effective={
   region:d.effective?.region??region,
   delay:d.effective?.delay??delay,
   universe:d.effective?.universe??universe,
  };
  const incoming=(d.rows||[]).map((x:any)=>({...x,...effective}));
  setRegion(effective.region);setDelay(effective.delay);setUniverse(effective.universe);
  const key=(f:any)=>`${f.id}|${f.dataset_id||""}|${f.region||effective.region}|${f.delay??effective.delay}`;
  const map=new Map<string,any>(fields.map(f=>[key(f),f]));
  incoming.forEach((f: Field) => map.set(key(f), f));
  const merged=[...map.values()].filter(f=>ids.includes(f.dataset_id));
  setFields(merged);sync(datasets,merged,ids,effective);
 }
 function toggle(id:string){const n=selected.includes(id)?selected.filter(x=>x!==id):[...selected,id];setSelected(n);fetchFields(n)}
 const shown=useMemo(()=>datasets.filter(x=>!search||`${x.id} ${x.name||""} ${x.category_name||x.category_id||""}`.toLowerCase().includes(search.toLowerCase())),[datasets,search]);
 return <div className="dx-stack">
  <div className="panel" style={{padding:14}}><div className="dx-head"><b>BRAIN Data Catalogue</b><span className="mut">Only datasets and fields verified from BRAIN are fed into ACE research.</span></div>
   <div style={{display:"flex",gap:8,marginTop:10,flexWrap:"wrap"}}><input value={region} onChange={e=>setRegion(e.target.value.toUpperCase())} placeholder="Region" style={{width:100}}/><input type="number" value={delay} onChange={e=>setDelay(Number(e.target.value))} style={{width:80}}/><input value={universe} onChange={e=>setUniverse(e.target.value)} style={{width:130}}/><input value={search} onChange={e=>setSearch(e.target.value)} placeholder="Search dataset" style={{flex:1,minWidth:180}}/><button className="btn sm" onClick={fetchDs} disabled={loading}>{loading?"Fetching…":"Fetch datasets"}</button></div>
   <div className="mut" style={{fontSize:11,marginTop:7}}>{loading&&loadingMsg?loadingMsg:`Session: ${status?.ready?"connected":"not connected"}. ${datasets.length} dataset(s), ${fields.length} field(s) in the current research context.`}</div></div>
  <div className="dx-split" style={{flex:1,minHeight:0}}><div className="panel dx-panel" style={{overflow:"auto"}}><div className="dx-head"><b>Datasets</b><span className="mut">{shown.length}</span></div><table><thead><tr><th></th><th>ID</th><th>Name</th><th>Category</th><th>Coverage</th></tr></thead><tbody>{shown.map(d=><tr key={`${d.id}-${d.region||region}-${d.delay??delay}`}><td><input type="checkbox" checked={selected.includes(d.id)} onChange={()=>toggle(d.id)}/></td><td><code>{d.id}</code></td><td>{d.name}</td><td>{d.category_name||d.category_id||"—"}</td><td>{d.coverage==null?"—":Number(d.coverage).toFixed(2)}</td></tr>)}</tbody></table>{!shown.length&&<div className="empty">Fetch datasets from BRAIN, then select one or more to inspect fields.</div>}</div>
   <div className="panel dx-panel" style={{overflow:"auto"}}><div className="dx-head"><b>Fields</b><span className="mut">{fields.length}</span></div><table><thead><tr><th>Field</th><th>Dataset</th><th>Type</th><th>Virgin</th><th>Description</th></tr></thead><tbody>{fields.map(f=><tr key={`${f.id}-${f.dataset_id}-${f.region||region}-${f.delay??delay}`}><td><code>{f.id}</code></td><td>{f.dataset_id}</td><td>{f.type}</td><td>{f.is_virgin?"yes":"no"}</td><td>{f.description}</td></tr>)}</tbody></table>{!fields.length&&<div className="empty">Select one or more datasets to load and merge their fields.</div>}</div></div></div>
}
