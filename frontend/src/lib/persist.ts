import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";

// Drop-in replacement for useState that mirrors the value to localStorage, so a screen's
// working state (simulation config, template text, generated expressions, super-alpha setup…)
// survives navigation AND a full page reload. State only changes when the user changes it or
// a fresh generation replaces it — never on mount/return. Namespaced under ace2- keys.
export function usePersistentState<T>(key: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const storageKey = `ace2:${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw !== null ? (JSON.parse(raw) as T) : initial;
    } catch {
      return initial;
    }
  });

  // Persist on every change. Guard the first run so we don't rewrite the same value we just read.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) { first.current = false; return; }
    try { localStorage.setItem(storageKey, JSON.stringify(value)); } catch {}
  }, [storageKey, value]);

  return [value, setValue];
}
