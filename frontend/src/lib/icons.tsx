// Minimal inline icon set (stroke-based, currentColor) referenced by the backend nav map.
// Keeping them here avoids an icon-font dependency and keeps the bundle tiny.
import type { JSX } from "react";

const P = (d: string) => (
  <svg viewBox="0 0 24 24" width="17" height="17" fill="none"
       stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
    <path d={d} />
  </svg>
);

export const ICONS: Record<string, JSX.Element> = {
  grid: P("M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z"),
  database: P("M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"),
  share: P("M7 12a3 3 0 100-.01M17 6a3 3 0 100-.01M17 18a3 3 0 100-.01M9.5 10.5l5-3M9.5 13.5l5 3"),
  flask: P("M9 3h6M10 3v6l-5 9a2 2 0 002 3h10a2 2 0 002-3l-5-9V3"),
  compass: P("M12 22a10 10 0 100-20 10 10 0 000 20zM16 8l-2 6-6 2 2-6z"),
  bookmark: P("M6 3h12v18l-6-4-6 4z"),
  layout: P("M4 4h16v4H4zM4 12h7v8H4zM15 12h5v8h-5z"),
  sparkles: P("M13 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2zM5 3v4M3 5h4"),
  play: P("M6 4l14 8-14 8z"),
  star: P("M12 3l2.6 6.3L21 10l-5 4.3L17.5 21 12 17.5 6.5 21 8 14.3 3 10l6.4-.7z"),
  chart: P("M3 3v18h18M8 14l3-4 3 3 4-6"),
  grid2: P("M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"),
  list: P("M4 7h16M4 12h16M4 17h16"),
  globe: P("M12 22a10 10 0 100-20 10 10 0 000 20zM2 12h20M12 2c3 3 3 17 0 20M12 2c-3 3-3 17 0 20"),
  heart: P("M12 20s-7-4.6-9.2-8.5C1.3 8.9 2.6 5.5 6 5.5c2 0 3.2 1.2 4 2.3.8-1.1 2-2.3 4-2.3 3.4 0 4.7 3.4 3.2 6C19 15.4 12 20 12 20z"),
  send: P("M4 4l16 8-16 8 3-8zM7 12h13"),
  settings: P("M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2-1.6-2-3.4-2.4 1a7 7 0 00-1.7-1L14.5 4h-4l-.3 2.4a7 7 0 00-1.7 1l-2.4-1-2 3.4L4 12l-2 1.6 2 3.4 2.4-1a7 7 0 001.7 1l.3 2h4l.3-2a7 7 0 001.7-1l2.4 1 2-3.4z"),
  help: P("M12 22a10 10 0 100-20 10 10 0 000 20zM9.2 9.2a2.8 2.8 0 114.3 2.7c-.9.6-1.5 1-1.5 2.1M12 17h.01"),
};

export const icon = (name: string) => ICONS[name] ?? ICONS.grid;
