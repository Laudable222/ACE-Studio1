import { useState } from "react";
import { api } from "../lib/api";
import { useSession } from "../lib/session";
import { useToast } from "../lib/toast";
import "./login.css";

// Global login prompt. A banner appears on every screen whenever no BRAIN session exists, and
// opens a dialog that runs the two-step biometrics login from anywhere in the app. It NEVER
// prompts or logs in while a session is present — that's the studio's core invariant.
export function LoginGate() {
  const { s, loading, loginOpen, openLogin, closeLogin, refresh } = useSession();
  // Show the prompt only once the first status has resolved and it's genuinely absent.
  const needsLogin = !loading && !s.ok;

  return (
    <>
      {needsLogin ? (
        <div className="login-banner">
          <span>🔒 <b>No BRAIN session.</b> Datasets, fields, simulations and results need you signed in.</span>
          <span className="grow" />
          <button className="btn sm" onClick={openLogin}>Log in</button>
        </div>
      ) : null}
      {loginOpen ? <LoginModal onClose={closeLogin} onDone={refresh} /> : null}
    </>
  );
}

function LoginModal({ onClose, onDone }: { onClose: () => void; onDone: () => Promise<void> }) {
  const { toast, toastErr } = useToast();
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [msg, setMsg] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);

  async function begin() {
    setBusy(true); setMsg("Contacting BRAIN… (this can take 10–30s)"); setUrl("");
    const r = await api.post<any>("/settings/login/begin", { email: email || null, password: pass || null });
    setBusy(false);
    if (r.error) { setMsg(""); return toastErr(r.error); }
    if (r.state === "done") { setMsg("Logged in — session saved."); toast("Logged in."); await onDone(); return void onClose(); }
    if (r.state === "biometrics" && r.url) {
      setUrl(r.url);
      setMsg("Biometrics required. Open the link below, complete it in your browser, then click Finish login.");
      window.open(r.url, "_blank", "noopener");     // pop the biometrics page for them
      return;
    }
    setMsg(r.message || "Unexpected response. Try again.");
  }

  async function finish() {
    setBusy(true); setMsg("Confirming…");
    const r = await api.post<any>("/settings/login/complete");
    setBusy(false);
    if (r.error) { setMsg(""); return toastErr(r.error); }
    if (r.state === "done") { toast("Logged in."); await onDone(); return void onClose(); }
    setMsg(r.message || "Biometrics not finished yet — complete it in the browser, then click Finish again.");
  }

  return (
    <div className="login-overlay" onClick={onClose}>
      <div className="login-box" onClick={(e) => e.stopPropagation()}>
        <span className="close-x" onClick={onClose}>×</span>
        <h3>Sign in to WorldQuant BRAIN</h3>
        <div className="sub">
          Login is never automatic. Enter your BRAIN email and password (leave blank to reuse
          saved credentials), start the login, complete biometrics in your browser, then Finish.
          Credentials are stored only on this machine.
        </div>

        <label className="fld"><span>Email (blank uses saved)</span>
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoFocus /></label>
        <label className="fld"><span>Password</span>
          <input type="password" value={pass} onChange={(e) => setPass(e.target.value)} /></label>

        <div className="steps">
          <button className="btn" onClick={begin} disabled={busy}>
            {busy ? <span className="spin" /> : null} Begin login
          </button>
          <button className="btn ghost" onClick={finish} disabled={busy}>Finish login</button>
        </div>

        {msg ? (
          <div className="msg">
            {msg}
            {url ? <div style={{ marginTop: 8 }}>
              <a href={url} target="_blank" rel="noopener">Open biometrics link ↗</a>
            </div> : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
