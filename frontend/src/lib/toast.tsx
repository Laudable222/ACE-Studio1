import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { humanizeError } from "./humanize";

type Kind = "ok" | "warn" | "err";
interface Toast { id: number; msg: string; kind: Kind; }
interface ToastApi {
  toast: (msg: string, kind?: Kind) => void;
  toastErr: (raw: unknown) => void;
}

const Ctx = createContext<ToastApi>({ toast: () => {}, toastErr: () => {} });
export const useToast = () => useContext(Ctx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((msg: string, kind: Kind) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, msg, kind }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), 4200);
  }, []);
  const api: ToastApi = {
    toast: (m, k = "ok") => push(m, k),
    toastErr: (raw) => push(humanizeError(raw), "err"),
  };
  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts">
        {items.map((t) => (
          <div key={t.id} className={"toast " + t.kind}>{t.msg}</div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
