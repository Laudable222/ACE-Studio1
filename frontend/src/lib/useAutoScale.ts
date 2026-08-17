import { useEffect } from "react";

// Keeps the whole UI scroll-free by shrinking it when the viewport is smaller than the
// design baseline (e.g. at 125%+ OS zoom, which reduces the CSS pixel viewport). We scale
// DOWN aggressively but never UP past 1, so the layout stays compact on big screens too.
// Design baseline the compact layout is tuned for. When the CSS viewport is smaller than
// this — which is exactly what a 125%+ Windows display scale produces — we shrink the whole
// UI so nothing overflows the screen. We never scale UP past 1.
const BASE_W = 1240;
const BASE_H = 720;
const MIN = 0.55;

export function useAutoScale() {
  useEffect(() => {
    const apply = () => {
      const s = Math.min(1, window.innerWidth / BASE_W, window.innerHeight / BASE_H);
      const clamped = Math.max(MIN, Math.floor(s * 100) / 100);
      document.documentElement.style.setProperty("--ui-scale", String(clamped));
    };
    apply();
    window.addEventListener("resize", apply);
    // devicePixelRatio changes when the OS display scale changes at runtime.
    const mq = window.matchMedia(`(resolution: ${window.devicePixelRatio}dppx)`);
    mq.addEventListener?.("change", apply);
    return () => { window.removeEventListener("resize", apply); mq.removeEventListener?.("change", apply); };
  }, []);
}
