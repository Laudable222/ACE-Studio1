import { useEffect, useState, type ComponentType } from "react";
import { Routes, Route, useLocation } from "react-router-dom";
import { ErrorBoundary } from "./ErrorBoundary";
import { Sidebar } from "./layout/Sidebar";
import { Header } from "./layout/Header";
import { CommandCenter } from "../features/command/CommandCenter";
import { DataExplorer } from "../features/data/DataExplorer";
import { KnowledgeGraph } from "../features/knowledge/KnowledgeGraph";
import { ResearchLab } from "../features/research/ResearchLab";
import { ResearchEngine } from "../features/discovery/ResearchEngine";
import { SubmissionManager } from "../features/submission/SubmissionManager";
import { AlphaEvolution } from "../features/evolution/AlphaEvolution";
import { AlphaReplication } from "../features/replication/AlphaReplication";
import { Generation } from "../features/generate/Generation";
import { Simulation } from "../features/simulate/Simulation";
import { ResultsAnalytics } from "../features/results/ResultsAnalytics";
import { Portfolio } from "../features/portfolio/Portfolio";
import { PromptLibrary } from "../features/prompts/PromptLibrary";
import { SuperAlpha } from "../features/super/SuperAlpha";
import { OperatorAtlas } from "../features/operators/OperatorAtlas";
import { Settings } from "../features/settings/Settings";
import { StrategyAtlas } from "../features/strategy/StrategyAtlas";
import { RegionAtlas } from "../features/regions/RegionAtlas";
import { SuccessDonation } from "../features/tier/SuccessDonation";
import { TemplateStudio } from "../features/templates/TemplateStudio";
import { useTheme } from "../lib/useTheme";
import { Placeholder } from "../features/common/Placeholder";
import { useAutoScale } from "../lib/useAutoScale";
import { ResearchProvider } from "../lib/store";
import { ToastProvider } from "../lib/toast";
import { SessionProvider, useSession } from "../lib/session";
import { LoginGate } from "./LoginGate";
import { JobTray } from "./JobTray";
import { DailyQuote } from "./DailyQuote";
import { CommandPalette } from "./CommandPalette";
import { api } from "../lib/api";
import type { NavGroup } from "./types";
import "./shell.css";

const SCREENS: Record<string, ComponentType> = {
  "/": CommandCenter,
  "/data": DataExplorer,
  "/knowledge": KnowledgeGraph,
  "/research": ResearchLab,
  "/discovery": ResearchEngine,
  "/submission": SubmissionManager,
  "/evolution": AlphaEvolution,
  "/replication": AlphaReplication,
  "/generate": Generation,
  "/simulate": Simulation,
  "/results": ResultsAnalytics,
  "/portfolio": Portfolio,
  "/prompts": PromptLibrary,
  "/super": SuperAlpha,
  "/operators": OperatorAtlas,
  "/settings": Settings,
  "/strategies": StrategyAtlas,
  "/regions": RegionAtlas,
  "/tier": SuccessDonation,
  "/templates": TemplateStudio,
};

export function App() {
  useAutoScale();
  useTheme();   // apply the remembered light/dark theme on load
  const [nav, setNav] = useState<NavGroup[]>([]);
  const [cmdOpen, setCmdOpen] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => { api.get<{ nav: NavGroup[] }>("/meta/nav").then((d) => setNav(d.nav ?? [])); }, []);
  const items = nav.flatMap((g) => g.items);

  return (
    <ToastProvider>
      <ResearchProvider>
        <SessionProvider>
        <div className="shell">
          <Sidebar nav={nav} />
          <div className="shell-main">
            <Header nav={nav} onSearch={() => setCmdOpen(true)} />
            <DailyQuote />
            <LoginGate />
            <main className="shell-content">
              <ErrorBoundary key={pathname}>
                <Routes>
                  {items.map((it) => {
                    const Screen = SCREENS[it.path];
                    return (
                      <Route key={it.id} path={it.path}
                        element={Screen ? <Screen /> : <Placeholder title={it.label} ready={it.ready} />} />
                    );
                  })}
                  <Route path="/" element={<CommandCenter />} />
                  <Route path="*" element={<Placeholder title="Not found" ready={false} />} />
                </Routes>
              </ErrorBoundary>
            </main>
            <ShellFooter />
          </div>
        </div>
        <CommandPalette nav={nav} open={cmdOpen} setOpen={setCmdOpen} />
        <JobTray />
        </SessionProvider>
      </ResearchProvider>
    </ToastProvider>
  );
}

// Footer is hidden while the "no session" login banner is up, so the yellow prompt owns the
// bottom of the shell and nothing competes with it. It returns once a session exists.
function ShellFooter() {
  const { s, loading } = useSession();
  if (!loading && !s.ok) return null;
  return (
    <footer className="shell-foot">
      <span>ACE Studio</span><span className="sep">·</span>
      <span>Local Alpha Research Studio</span>
      <span className="right">Happy Research ✦ · Let's Shine Together!</span>
    </footer>
  );
}
