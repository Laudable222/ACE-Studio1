import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "./api";

// Live BRAIN-session status, shared app-wide. Polls the backend so any screen can react to
// login state, and drives the global login prompt. Invariant: we NEVER trigger a login when
// a session already exists — this only observes status and opens the dialog on request.
export interface SessionState {
  ok: boolean;            // true while a session is present (valid or a transient "unknown")
  state: string;          // "valid" | "unknown" | "expired" | "none" | "loading"
  remaining: number;
  remaining_human: string;
  status: string;
}

interface SessionCtx {
  s: SessionState;
  loading: boolean;       // true only until the FIRST status resolves (avoids a flash of the prompt)
  loginOpen: boolean;
  openLogin: () => void;
  closeLogin: () => void;
  refresh: () => Promise<void>;
}

const EMPTY: SessionState = { ok: false, state: "loading", remaining: 0, remaining_human: "", status: "" };
const Ctx = createContext<SessionCtx | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [s, setS] = useState<SessionState>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [loginOpen, setLoginOpen] = useState(false);
  const timer = useRef<number | null>(null);

  const refresh = async () => {
    const d = await api.get<any>("/session/status");
    // A 404/degraded backend returns { error } — treat that as "not logged in" rather than crashing.
    if (d?.error || typeof d?.ok !== "boolean") {
      setS({ ok: false, state: "none", remaining: 0, remaining_human: "", status: d?.error || "" });
    } else {
      setS({ ok: !!d.ok, state: d.state || "none", remaining: d.remaining || 0,
             remaining_human: d.remaining_human || "", status: d.status || "" });
    }
    setLoading(false);
  };

  useEffect(() => {
    refresh();
    // Re-check periodically so an expiry (or a login done elsewhere) is reflected without a reload.
    timer.current = window.setInterval(refresh, 30000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, []);

  const value: SessionCtx = {
    s, loading, loginOpen,
    openLogin: () => setLoginOpen(true),
    closeLogin: () => setLoginOpen(false),
    refresh,
  };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): SessionCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useSession must be used within a SessionProvider");
  return c;
}
