import { Component, type ReactNode } from "react";

// Catches any render error in a screen and shows a friendly panel instead of a blank app,
// so one screen's problem never takes the whole studio down.
export class ErrorBoundary extends Component<{ children: ReactNode }, { err: Error | null }> {
  state = { err: null as Error | null };
  static getDerivedStateFromError(err: Error) { return { err }; }
  componentDidCatch(err: Error) { console.error("screen error:", err); }
  reset = () => this.setState({ err: null });

  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="panel" style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
        <div style={{ textAlign: "center", maxWidth: 460 }}>
          <div style={{ fontSize: 17, fontWeight: 600 }}>This screen hit a snag</div>
          <div className="mut" style={{ marginTop: 6 }}>
            Something on this screen errored. This is usually because the backend isn't
            reachable — make sure it's running on port 8766 (start it with <code>run.bat</code>).
          </div>
          <div className="mut" style={{ fontSize: 11, marginTop: 8 }}>{String(this.state.err?.message || this.state.err)}</div>
          <button className="btn" style={{ marginTop: 14 }} onClick={this.reset}>Try again</button>
        </div>
      </div>
    );
  }
}
