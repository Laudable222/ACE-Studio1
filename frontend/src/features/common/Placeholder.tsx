// Every not-yet-built screen renders this, so the whole navigation is walkable from day
// one and each phase simply swaps a placeholder for its real feature.
export function Placeholder({ title, ready }: { title: string; ready: boolean }) {
  return (
    <div className="panel" style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
      <div style={{ textAlign: "center", padding: 24 }}>
        <div style={{ fontSize: 17, fontWeight: 600 }}>{title}</div>
        <div className="mut" style={{ marginTop: 6, maxWidth: 420 }}>
          {ready
            ? "This screen is under construction."
            : "Planned screen. It arrives in a later build phase — the navigation and shell are in place so features drop straight in."}
        </div>
        <span className="badge" style={{ marginTop: 12, background: "var(--acc-weak)", color: "var(--acc)" }}>
          coming soon
        </span>
      </div>
    </div>
  );
}
