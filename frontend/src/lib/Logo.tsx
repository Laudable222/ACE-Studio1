// ACE Studio mark — a rounded badge holding a stylized rising "alpha" signal: a clean,
// scalable, professional identity for an alpha-research studio. Uses the brand gradient.
export function LogoMark({ size = 26 }: { size?: number }) {
  const id = "ace-grad";
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" xmlns="http://www.w3.org/2000/svg" aria-label="ACE Studio">
      <defs>
        <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--acc-2)" />
          <stop offset="1" stopColor="var(--acc)" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8.5" fill={`url(#${id})`} />
      {/* rising signal / alpha curve */}
      <path d="M6.5 21.5 L12.5 13 L17.5 17.5 L25 8"
        fill="none" stroke="var(--acc-fg)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" opacity="0.92" />
      <circle cx="25" cy="8" r="2.4" fill="var(--acc-fg)" />
      <circle cx="6.5" cy="21.5" r="1.8" fill="var(--acc-fg)" opacity="0.7" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 9, fontWeight: 700, fontSize: 16, letterSpacing: "-0.2px" }}>
      <LogoMark />
      <span>ACE <span style={{ color: "var(--acc)" }}>Studio</span></span>
    </span>
  );
}
