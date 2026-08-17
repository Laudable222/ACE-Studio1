import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api";
import { useToast } from "../lib/toast";
import "./jobtray.css";

interface Job { id: string; kind: string; status: string; message: string; total: number; done: number; created: number; }

const LABEL: Record<string, string> = {
  generate: "Generation", simulate: "Simulation", research: "Research", strategy: "Strategy Atlas",
  super: "SuperAlpha", rewrite: "Master prompt", "push-prompt": "Save prompt", "knowledge-judge": "AI verify",
  "knowledge-xregion": "Cross-region", "strategy-sweep": "Cross-region sweep",
};
const label = (k: string) => LABEL[k] || k;

// A persistent, app-wide tray of background jobs. Polls /meta/jobs so a long run is visible on
// every screen, and toasts when a job finishes — so work is never tied to one screen.
export function JobTray() {
  const { toast, toastErr } = useToast();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [open, setOpen] = useState(true);
  const prev = useRef<Record<string, string>>({});
  const seeded = useRef(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      const d = await api.get<{ jobs: Job[] }>("/meta/jobs");
      if (!alive || d.error) return;
      const list = d.jobs || [];
      // Notify on transitions to done/error (skip the very first load so old jobs don't toast).
      if (seeded.current) {
        for (const j of list) {
          const was = prev.current[j.id];
          if (was === "running" && j.status !== "running") {
            if (j.status === "done") toast(`✓ ${label(j.kind)} finished.`, "ok");
            else if (j.status === "error") toastErr(`${label(j.kind)} failed.`);
          }
        }
      }
      prev.current = Object.fromEntries(list.map((j) => [j.id, j.status]));
      seeded.current = true;
      setJobs(list);
    };
    tick();
    const t = window.setInterval(tick, 2000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  const running = jobs.filter((j) => j.status === "running");
  if (!running.length) return null;

  return (
    <div className="jobtray">
      <div className="jobtray-head" onClick={() => setOpen((v) => !v)}>
        <span className="spin" /> <b>{running.length}</b> running <span className="jobtray-caret">{open ? "▾" : "▸"}</span>
      </div>
      {open ? (
        <div className="jobtray-list">
          {running.map((j) => (
            <div key={j.id} className="jobtray-item">
              <div className="jobtray-kind">{label(j.kind)}</div>
              <div className="jobtray-msg">{j.message || "working…"}{j.total ? ` · ${j.done}/${j.total}` : ""}</div>
              {j.total ? <div className="jobtray-bar"><div style={{ width: `${Math.round(100 * j.done / Math.max(1, j.total))}%` }} /></div> : null}
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
