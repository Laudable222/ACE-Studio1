import { useEffect, useState } from "react";
import { api } from "./api";

export interface Session { ok: boolean; state?: string; remaining?: number; remaining_human?: string; }

// Polls session status every 30s, but ONLY while the tab is visible (a backgrounded tab
// makes no BRAIN auth calls), and re-checks immediately on return — same discipline as v1.
export function useSession(): Session {
  const [sess, setSess] = useState<Session>({ ok: false });
  useEffect(() => {
    let last = 0;
    const poll = async () => {
      last = Date.now();
      const d = await api.get<Session>("/session/status");
      setSess(d?.error ? { ok: false } : d);
    };
    poll();
    const iv = setInterval(() => { if (!document.hidden) poll(); }, 30000);
    const onVis = () => { if (!document.hidden && Date.now() - last > 5000) poll(); };
    document.addEventListener("visibilitychange", onVis);
    return () => { clearInterval(iv); document.removeEventListener("visibilitychange", onVis); };
  }, []);
  return sess;
}
