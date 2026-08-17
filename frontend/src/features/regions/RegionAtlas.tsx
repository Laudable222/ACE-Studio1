import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useResearch } from "../../lib/store";
import { useToast } from "../../lib/toast";

interface Reg {
  region: string; delays: number[]; universes: string[]; neutralizations: string[];
  datasets_known: number; fields_known: number; cross_region_datasets: number;
  delay0: boolean; note: string;
}

// Region & Universe Atlas: what each market is like, what's available, how much the studio
// has learned there, and where ideas transfer across regions. Set your working region here.
export function RegionAtlas() {
  const R = useResearch();
  const { toast } = useToast();
  const [regions, setRegions] = useState<Reg[]>([]);
  const [sel, setSel] = useState<string>(R.ctx.region);

  useEffect(() => { api.get<{ regions: Reg[] }>("/regions/info").then((d) => setRegions(d.regions || [])); }, []);
  const cur = regions.find((r) => r.region === sel);

  function useRegion(r: Reg) {
    // Robust even when options are thin: fall back to a sensible delay/universe so a
    // dataset fetch from the Data Explorer always has a valid context.
    const delay = (r.delays && r.delays.includes(R.ctx.delay)) ? R.ctx.delay : (r.delays?.[0] ?? 1);
    const universe = (r.universes && r.universes[0]) || R.ctx.universe || "TOP3000";
    R.setCtx({ region: r.region, delay, universe });
    toast(`Working region set to ${r.region} · ${universe} · D${delay}. Fetch datasets in the Data Explorer.`);
  }

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      <div className="panel panel-scroll" style={{ padding: 14 }}>
        <div className="dx-head"><b>Regions</b><span className="mut">{regions.length}</span></div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: 8 }}>
          {regions.map((r) => (
            <div key={r.region} onClick={() => setSel(r.region)} className="panel"
              style={{ padding: "10px 12px", boxShadow: "none", cursor: "pointer",
                borderColor: sel === r.region ? "var(--acc)" : "var(--line)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <b>{r.region}</b>
                {r.delay0 ? <span className="badge" style={{ background: "var(--acc-weak)", color: "var(--acc)" }}>D0</span> : null}
                {R.ctx.region === r.region ? <span className="badge ok">active</span> : null}
              </div>
              <div className="mut" style={{ fontSize: 11, marginTop: 3 }}>
                {r.datasets_known} datasets · {r.fields_known} fields known
                {r.cross_region_datasets ? ` · ${r.cross_region_datasets} shared` : ""}
              </div>
            </div>))}
        </div>
      </div>

      <div className="panel panel-scroll" style={{ padding: 14 }}>
        <div className="dx-head"><b>{cur ? cur.region : "Region"}</b>
          {cur ? <button className="btn sm" style={{ marginLeft: "auto" }} onClick={() => useRegion(cur)}>Use this region</button> : null}</div>
        {!cur ? <div className="empty">Pick a region.</div> :
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <div className="mut" style={{ fontSize: 13, lineHeight: 1.6 }}>{cur.note}</div>
            {cur.delay0 ? <div className="panel" style={{ padding: "9px 11px", boxShadow: "none", background: "var(--acc-weak)", borderColor: "var(--acc)" }}>
              <b style={{ color: "var(--acc)" }}>Delay 0 available</b>
              <div className="mut" style={{ fontSize: 12 }}>Delay-0 alphas are judged on Sharpe — the gate here is stricter (Sharpe 2.69 / Fitness 1.5).</div>
            </div> : null}
            <div>
              <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 4 }}>Delays</div>
              <div className="dx-filters wrap">{cur.delays.map((d) => <span key={d} className="chip">D{d}</span>)}</div>
            </div>
            <div>
              <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 4 }}>Universes</div>
              <div className="dx-filters wrap">{cur.universes.map((u) => <span key={u} className="chip">{u}</span>)}</div>
            </div>
            <div>
              <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px", marginBottom: 4 }}>Neutralizations</div>
              <div className="dx-filters wrap">{cur.neutralizations.map((n) => <span key={n} className="chip">{n}</span>)}</div>
            </div>
            {cur.cross_region_datasets ? <div className="mut" style={{ fontSize: 12 }}>
              {cur.cross_region_datasets} of this region's datasets also exist elsewhere — the Knowledge Graph shows exactly where, so you can replay a winning idea across regions.
            </div> : null}
          </div>}
      </div>
    </div>
  );
}
