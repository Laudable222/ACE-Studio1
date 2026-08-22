import { useLocation } from "react-router-dom";
import { useResearch } from "../../lib/store";
import { useTheme } from "../../lib/useTheme";
import type { NavGroup } from "../types";

export function Header({ nav, onSearch }: { nav: NavGroup[]; onSearch: () => void }) {
  const { pathname } = useLocation();
  const { ctx } = useResearch();
  const { theme, setTheme } = useTheme();
  const item = nav.flatMap((g) => g.items).find((it) => it.path === pathname);
  const current = item?.label ?? "ACE Studio";

  return (
    <header className="header">
      <div className="header-titles">
        <span className="title">{current}</span>
        {item?.description ? <span className="subtitle">{item.description}</span> : null}
      </div>
      <div className="spacer" />
      {/* Live research context (edit it on the Data Explorer). */}
      <span className="ctx-chip">{ctx.region} · {ctx.universe} · <b>D{ctx.delay}</b> · {ctx.instrument}</span>
      <button className="icon-btn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
        title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"} aria-label="Toggle theme">
        {theme === "dark" ?
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" /></svg> :
          <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" /></svg>}
      </button>
      <span className="cmdk" onClick={onSearch} title="Search this app (screens & actions)">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4-4" /></svg>
        Search <kbd>⌘K</kbd>
      </span>
    </header>
  );
}
