import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import type { NavGroup } from "./types";
import "./cmdk.css";

// In-app command palette (⌘K / Ctrl-K, or the header search). Searches ONLY the app's own
// screens/actions — it never queries the BRAIN API — and navigates on Enter.
export function CommandPalette({ nav, open, setOpen }: { nav: NavGroup[]; open: boolean; setOpen: (v: boolean) => void }) {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [i, setI] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const items = useMemo(() =>
    nav.flatMap((g) => g.items.map((it) => ({ ...it, group: g.group }))), [nav]);

  const results = useMemo(() => {
    const s = q.toLowerCase().trim();
    if (!s) return items;
    return items.filter((it) => it.label.toLowerCase().includes(s) || it.group.toLowerCase().includes(s));
  }, [items, q]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen(true); }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [setOpen]);

  useEffect(() => { if (open) { setQ(""); setI(0); setTimeout(() => inputRef.current?.focus(), 10); } }, [open]);
  useEffect(() => { setI(0); }, [q]);

  if (!open) return null;

  const go = (path: string) => { setOpen(false); navigate(path); };

  return (
    <div className="cmdk-overlay" onClick={() => setOpen(false)}>
      <div className="cmdk-box" onClick={(e) => e.stopPropagation()}>
        <input ref={inputRef} className="cmdk-input" placeholder="Search screens & actions…" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setI((x) => Math.min(x + 1, results.length - 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setI((x) => Math.max(x - 1, 0)); }
            if (e.key === "Enter" && results[i]) go(results[i].path);
          }} />
        <div className="cmdk-list">
          {!results.length ? <div className="cmdk-empty">No matching screen.</div> :
            results.map((r, idx) => (
              <div key={r.id} className={"cmdk-item" + (idx === i ? " active" : "")}
                onMouseEnter={() => setI(idx)} onClick={() => go(r.path)}>
                <span>{r.label}</span>
                <span className="cmdk-group">{r.group}{!r.ready ? " · soon" : ""}</span>
              </div>))}
        </div>
        <div className="cmdk-foot">↑↓ to move · Enter to open · Esc to close · searches this app only</div>
      </div>
    </div>
  );
}
