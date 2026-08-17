import { NavLink } from "react-router-dom";
import { icon } from "../../lib/icons";
import { useSession } from "../../lib/useSession";
import { usePersistentState } from "../../lib/persist";
import { Wordmark } from "../../lib/Logo";
import type { NavGroup } from "../types";

// The sidebar collapses to an icon-only rail. The toggle sits at the TOP of the rail when
// collapsed, and at the TOP-RIGHT of the full sidebar when open — never in the header.
export function Sidebar({ nav }: { nav: NavGroup[] }) {
  const sess = useSession();
  const [collapsed, setCollapsed] = usePersistentState("ui:sidebar-collapsed", false);

  const Toggle = () => (
    <button className="sidebar-toggle" onClick={() => setCollapsed((v) => !v)}
      title={collapsed ? "Expand sidebar" : "Collapse sidebar"} aria-label="Toggle sidebar">
      <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        {collapsed ? <path d="M9 6l6 6-6 6" /> : <path d="M15 6l-6 6 6 6" />}
      </svg>
    </button>
  );

  return (
    <aside className={"sidebar" + (collapsed ? " collapsed" : "")}>
      <div className="sidebar-top">
        {!collapsed ? <div className="sidebar-brand"><Wordmark /></div> : null}
        <Toggle />
      </div>

      <nav className="sidebar-nav">
        {nav.map((g) => (
          <div key={g.group}>
            {!collapsed ? <div className="nav-group-label">{g.group}</div> : null}
            {g.items.map((it) => (
              <NavLink key={it.id} to={it.path} end={it.path === "/"} title={collapsed ? it.label : undefined}
                className={({ isActive }) =>
                  "nav-link" + (isActive ? " active" : "") + (it.ready ? "" : " soon")}>
                {icon(it.icon)}
                {!collapsed ? <span>{it.label}</span> : null}
                {!collapsed && !it.ready ? <span className="soon-dot">soon</span> : null}
              </NavLink>
            ))}
          </div>
        ))}
      </nav>

      {!collapsed ? (
        <div className="sidebar-foot">
          <span className="lbl"><span className={"dot" + (sess.ok ? " ok" : "")} /> Session</span>
          <b>{sess.ok ? (sess.remaining_human || "active") : "not connected"}</b>
        </div>
      ) : (
        <div className="sidebar-foot-mini" title={sess.ok ? "Session active" : "Not connected"}>
          <span className={"dot" + (sess.ok ? " ok" : "")} />
        </div>
      )}
    </aside>
  );
}
