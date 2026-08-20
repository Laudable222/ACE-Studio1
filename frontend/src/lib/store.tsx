import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { api } from "./api";

// The research context that flows into every screen: market coordinates plus the working
// set of datasets and fields. Persisted to localStorage so a reload never loses the setup.

export interface Ctx { instrument: string; region: string; delay: number; universe: string; }
export interface Dataset {
  id: string; name?: string; description?: string; coverage?: number;
  valueScore?: number; alphaCount?: number; category_id?: string; category_name?: string;
}
export interface Field {
  id: string; description?: string; type?: string; alphaCount?: number;
  is_virgin?: boolean; dataset_id?: string; prefix?: string;
}
export interface OptRecord {
  instrument: string; region: string; delay: number; universes: string[]; neutralizations: string[];
}

// A cross-screen guided-workflow handoff (e.g. Strategy Atlas → Data Explorer → Generation).
export interface Flow { goal: string; datasets: string[]; promptName: string; promptBody: string; }

interface Research {
  ctx: Ctx;
  setCtx: (patch: Partial<Ctx>) => void;
  options: OptRecord[];
  datasets: Dataset[]; setDatasets: (d: Dataset[]) => void;
  fields: Field[]; setFields: (f: Field[]) => void;
  selDatasets: string[]; setSelDatasets: (v: string[]) => void;
  selFields: string[]; setSelFields: (v: string[]) => void;
  pending: string[]; setPending: (v: string[]) => void;   // expressions handed off to Simulation
  pendingExperimentId: number | null; setPendingExperimentId: (v: number | null) => void;   // which Experiment (if any) those expressions came from, for sim_results provenance
  pendingFamilyId: number | null; setPendingFamilyId: (v: number | null) => void;   // an Alpha Evolution family just created elsewhere, to auto-select on the Evolution screen
  flow: Flow; setFlow: (f: Partial<Flow>) => void; clearFlow: () => void;
  // derived option helpers
  regions: (inst: string) => string[];
  delays: (inst: string, region: string) => number[];
  universes: (inst: string, region: string, delay: number) => string[];
  instruments: () => string[];
}

const DEFAULT_CTX: Ctx = { instrument: "EQUITY", region: "IND", delay: 1, universe: "TOP1000" };
const KEY = "ace2-research";

const ResearchCtx = createContext<Research>(null as unknown as Research);
export const useResearch = () => useContext(ResearchCtx);

function load(): any {
  try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch { return {}; }
}

export function ResearchProvider({ children }: { children: ReactNode }) {
  const saved = load();
  const [ctx, setCtxState] = useState<Ctx>({ ...DEFAULT_CTX, ...(saved.ctx || {}) });
  const [options, setOptions] = useState<OptRecord[]>([]);
  const [datasets, setDatasets] = useState<Dataset[]>(saved.datasets || []);
  const [fields, setFields] = useState<Field[]>(saved.fields || []);
  const [selDatasets, setSelDatasets] = useState<string[]>(saved.selDatasets || []);
  const [selFields, setSelFields] = useState<string[]>(saved.selFields || []);
  const [pending, setPending] = useState<string[]>(saved.pending || []);
  const [pendingExperimentId, setPendingExperimentId] = useState<number | null>(saved.pendingExperimentId ?? null);
  const [pendingFamilyId, setPendingFamilyId] = useState<number | null>(saved.pendingFamilyId ?? null);
  const EMPTY_FLOW: Flow = { goal: "", datasets: [], promptName: "", promptBody: "" };
  const [flow, setFlowState] = useState<Flow>(saved.flow || EMPTY_FLOW);

  useEffect(() => {
    api.get<{ records: OptRecord[] }>("/data/options").then((d) => setOptions(d.records || []));
  }, []);

  // persist (skip the big datasets/fields arrays if they blow the quota)
  useEffect(() => {
    const snap = { ctx, selDatasets, selFields, datasets, fields, pending, pendingExperimentId, pendingFamilyId, flow };
    try { localStorage.setItem(KEY, JSON.stringify(snap)); }
    catch { try { localStorage.setItem(KEY, JSON.stringify({ ctx, selDatasets, selFields, pending, pendingExperimentId, pendingFamilyId, flow })); } catch {} }
  }, [ctx, selDatasets, selFields, datasets, fields, pending, pendingExperimentId, pendingFamilyId, flow]);

  const setCtx = (patch: Partial<Ctx>) => setCtxState((c) => ({ ...c, ...patch }));
  const setFlow = (patch: Partial<Flow>) => setFlowState((f) => ({ ...f, ...patch }));
  const clearFlow = () => setFlowState(EMPTY_FLOW);

  const uniq = <T,>(a: T[]) => [...new Set(a)];
  const value = useMemo<Research>(() => ({
    ctx, setCtx, options, datasets, setDatasets, fields, setFields,
    selDatasets, setSelDatasets, selFields, setSelFields, pending, setPending,
    pendingExperimentId, setPendingExperimentId,
    pendingFamilyId, setPendingFamilyId,
    flow, setFlow, clearFlow,
    instruments: () => uniq(options.map((r) => r.instrument)),
    regions: (inst) => uniq(options.filter((r) => !inst || r.instrument === inst).map((r) => r.region)),
    delays: (inst, region) =>
      uniq(options.filter((r) => r.instrument === inst && r.region === region).map((r) => r.delay)).sort((a, b) => a - b),
    universes: (inst, region, delay) => {
      let rr = options.filter((r) => r.instrument === inst && r.region === region && r.delay === delay);
      if (!rr.length) rr = options.filter((r) => r.instrument === inst && r.region === region);
      return uniq(rr.flatMap((r) => r.universes));
    },
  }), [ctx, options, datasets, fields, selDatasets, selFields, pending, pendingExperimentId, pendingFamilyId, flow]);

  return <ResearchCtx.Provider value={value}>{children}</ResearchCtx.Provider>;
}
