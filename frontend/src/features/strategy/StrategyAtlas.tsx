import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { usePersistentState } from "../../lib/persist";
import { NumberInput } from "../../lib/NumberInput";
import { useToast } from "../../lib/toast";

interface DsRef { id: string; name?: string; }
interface Strat { name: string; thesis: string; build: string; datasets?: DsRef[]; }

const FALLBACK = ["Analyst", "Broker", "Earnings", "Fundamental", "Imbalance", "Insiders",
  "Institutions", "Macro", "Model", "News", "Option", "Price Volume", "Risk", "Sentiment",
  "Short Interest", "Social Media"];

// Strategy Atlas: the LLM explores the strategy space for a data category, seeded by your
// device so your set differs from everyone else's. Never a hardcoded list.
export function StrategyAtlas() {
  const R = useResearch();
  const nav = useNavigate();
  const { toast, toastErr } = useToast();
  const [cats, setCats] = useState<{ category: string; count: number }[]>([]);
  const [active, setActive] = usePersistentState("strategy:active", "");
  const [count, setCount] = usePersistentState("strategy:count", 6);
  const [twoCat, setTwoCat] = usePersistentState("strategy:twocat", false);
  const [busy, setBusy] = useState(false);
  const [pushBusy, setPushBusy] = useState("");   // strategy name currently being pushed
  const [strats, setStrats] = usePersistentState<Strat[]>("strategy:strats", []);
  const [meta, setMeta] = usePersistentState<{ provider?: string; datasets?: number; partner?: string }>("strategy:meta", {});

  useEffect(() => { api.get<any>("/strategy/categories").then((d) => setCats(d.categories || [])); }, []);
  const list = cats.length ? cats.map((c) => c.category) : FALLBACK.map((c) => c.toLowerCase());

  async function explore(cat: string) {
    setActive(cat); setBusy(true); setStrats([]);
    const start = await api.post<any>("/strategy/explore", { category: cat, region: R.ctx.region, delay: R.ctx.delay, instrument: R.ctx.instrument, n: Math.max(1, count), mode: twoCat ? "two_categories" : "single" });
    if (start.error || !start.job_id) { setBusy(false); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/strategy/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setBusy(false);
    if (s.status !== "done") return toastErr(s.error || s.status);
    setStrats(s.result?.strategies || []); setMeta({ provider: s.result?.provider, datasets: s.result?.datasets_explored, partner: s.result?.partner });
    const p = s.result?.partner;
    toast(p ? `${(s.result?.strategies || []).length} two-category strategies (${cat} + ${p}).`
      : `${(s.result?.strategies || []).length} strategies across ${s.result?.datasets_explored ?? 0} dataset(s) in ${cat}.`);
  }

  async function pushToResearch(st: Strat) {
    const ds = st.datasets || [];
    const ids = ds.map((d) => d.id);
    const label = ds.map((d) => d.name ? `${d.id} (${d.name})` : d.id).join(", ");
    const body = `Strategy: ${st.name}\nThesis: ${st.thesis}\nBuild: ${st.build}` +
      (ids.length ? `\nRequired datasets: ${label} — build alphas that test this thesis using the fields of these dataset(s).` : "");
    setPushBusy(st.name);
    const start = await api.post<any>("/research/push", {
      scope: "generate", category: active, region: R.ctx.region, body, dataset_names: ids,
      compose: true, source: "strategy",
    });
    if (start.error || !start.job_id) { setPushBusy(""); return toastErr(start.error || "Could not start."); }
    let s: any = {}; for (; ;) { s = await api.get(`/research/jobs/${start.job_id}`); if (s.status !== "running") break; await new Promise((r) => setTimeout(r, 1400)); }
    setPushBusy("");
    if (s.status !== "done") return toastErr(s.error || s.status);
    const r = s.result || {};
    // Start a guided flow: hand the strategy + its datasets to the Data Explorer, which walks the
    // user through fetching them and then on to Generation.
    R.setFlow({ goal: "generate", datasets: ids, promptName: r.name, promptBody: body });
    toast(ids.length
      ? `“${r.name}” saved. Taking you to the Data Explorer to fetch: ${ids.join(", ")}.`
      : `“${r.name}” saved. Fetch data, then generate.`, "ok");
    nav("/data");
  }

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      <div className="panel panel-scroll" style={{ padding: 14, maxWidth: 300 }}>
        <div className="dx-head"><b>Categories</b><span className="mut">{R.ctx.region} D{R.ctx.delay}</span>
          <label className="fld" style={{ width: 70, marginLeft: "auto" }}><span>Count</span>
            <NumberInput min={1} fallback={6} value={count} onChange={setCount} /></label></div>
        <div className="dx-filters" style={{ marginBottom: 8 }}>
          <span className={"pill" + (twoCat ? " on" : "")} onClick={() => setTwoCat((v) => !v)}
            title="Pairs the chosen category with the safest different-mechanism category (per the BRAIN combining-safely guide) for two-field strategies.">Two Categories</span>
          <span className="mut" style={{ fontSize: 11 }}>{twoCat ? "combines two safe categories" : "single category (default)"}</span>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {list.map((c) => (
            <div key={c} onClick={() => explore(c)}
              style={{ padding: "8px 10px", borderRadius: 8, cursor: "pointer", fontSize: 13,
                background: active === c ? "var(--acc-weak)" : "transparent", color: active === c ? "var(--acc)" : "var(--fg)" }}>
              {c}{cats.find((x) => x.category === c) ? <span className="mut" style={{ fontSize: 11 }}> · {cats.find((x) => x.category === c)!.count} datasets</span> : null}
            </div>))}
        </div>
      </div>

      <div className="panel panel-scroll" style={{ padding: 14 }}>
        <div className="dx-head"><b>{active ? `Strategies · ${active}${meta.partner ? ` + ${meta.partner}` : ""}` : "Strategies"}</b>
          {meta.provider ? <span className="mut">via {meta.provider}{meta.datasets ? ` · ${meta.datasets} datasets` : ""}</span> : null}</div>
        {busy ? <div className="mut"><span className="spin" /> Exploring every dataset in the category…</div> :
          !strats.length ? <div className="empty">Pick a category — the model explores its strategy space, seeded to your device so it is uniquely yours. Push any idea to build it.</div> :
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {strats.map((st, i) => (
                <div key={i} className="panel" style={{ padding: "11px 13px", boxShadow: "none" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <b style={{ color: "var(--acc)" }}>{st.name}</b>
                    <button className="btn ghost sm" style={{ marginLeft: "auto" }} onClick={() => pushToResearch(st)} disabled={!!pushBusy}>
                      {pushBusy === st.name ? <><span className="spin" /> Saving…</> : "Push to build →"}</button>
                  </div>
                  <div className="mut" style={{ fontSize: 12, marginTop: 4 }}>{st.thesis}</div>
                  {st.build ? <div style={{ fontSize: 12, marginTop: 4 }}><span className="mut">Build: </span>{st.build}</div> : null}
                  {st.datasets?.length ? <div className="dx-filters wrap" style={{ marginTop: 6 }}>
                    <span className="mut" style={{ fontSize: 11 }}>Datasets to fetch:</span>
                    {st.datasets.map((d) => <span key={d.id} className="chip" title={d.name || ""}><code>{d.id}</code></span>)}
                  </div> : null}
                </div>))}
            </div>}
      </div>
    </div>
  );
}
