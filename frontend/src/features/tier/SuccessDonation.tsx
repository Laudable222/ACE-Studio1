import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";

interface Tier {
  device_id: string; tier: string; is_supporter: boolean; features: string[];
  donate_url: string; min_donation: number; donation_visible?: boolean;
  success: { total: number; passed: number; success_rate: number };
}

// Success & Donation: the studio is free and never limits good research. When your verified
// success rate is high, you're invited to support from a small amount; a donating device
// unlocks scale/convenience. Entitlement is bound to this device by a signed licence.
export function SuccessDonation() {
  const { toast, toastErr } = useToast();
  const [t, setT] = useState<Tier | null>(null);
  const [lic, setLic] = useState("");
  const load = () => api.get<Tier>("/tier/status").then((d) => { if (!d.error) setT(d); });
  useEffect(() => { load(); }, []);

  const rate = t ? Math.round(t.success.success_rate * 100) : 0;
  const donationVisible = t?.donation_visible === true;   // hidden for now (backend flag)

  async function redeem() {
    let parsed: any;
    try { parsed = JSON.parse(lic); } catch { return toast("Paste the licence JSON exactly as issued.", "warn"); }
    const r = await api.post<any>("/tier/activate", { licence: parsed });
    if (r.error) return toastErr(r.error);
    if (r.activated) { toast("Unlocked — thank you for supporting ACE Studio.", "ok"); setLic(""); load(); }
    else toast(r.reason || "That licence didn't verify for this device.", "warn");
  }

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      <div className="panel panel-scroll" style={{ padding: 16 }}>
        <div className="dx-head"><b>Your success</b></div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: 12, marginTop: 4 }}>
          {[
            { l: "Success rate", v: `${rate}%` },
            { l: "Passed the gate", v: t?.success.passed ?? "—" },
            { l: "Simulated", v: t?.success.total ?? "—" },
          ].map((k) => (
            <div key={k.l} className="panel" style={{ padding: 14, boxShadow: "none" }}>
              <div className="mut" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".5px" }}>{k.l}</div>
              <div style={{ fontSize: 26, fontWeight: 700, color: "var(--acc)", marginTop: 4 }}>{k.v}</div>
            </div>))}
        </div>
        <div className="mut" style={{ fontSize: 13, lineHeight: 1.7, marginTop: 14 }}>
          A verified success means an alpha cleared <b>every</b> gate metric — Sharpe, Fitness, turnover,
          in-sample tests, and (with the submission check) self / prod / powerpool correlation below 0.70 —
          in absolute value, with delay-0 held to Sharpe 2.69 / Fitness 1.5. The app never limits your
          research; these numbers just reflect how it's actually helping you.
        </div>
      </div>

      <div className="panel panel-scroll" style={{ padding: 16 }}>
        <div className="dx-head"><b>{t?.is_supporter ? "Supporter" : "This device"}</b>
          {t ? <span className="badge" style={{ background: t.is_supporter ? "var(--ok-weak)" : "var(--faint)", color: t.is_supporter ? "var(--ok)" : "var(--mut)", marginLeft: "auto" }}>{t.tier}</span> : null}</div>

        {t?.is_supporter ?
          <div className="mut" style={{ fontSize: 13, lineHeight: 1.7 }}>
            Thank you — this device is a supporter. Unlocked: {t.features.join(", ") || "scale & convenience features"}.
          </div> :
          <div className="mut" style={{ fontSize: 13, lineHeight: 1.7 }}>
            The studio is <b>free and fully capable</b> — it never limits your research. Everything on every
            screen is available on this device.
          </div>}

        {/* Donation ask is hidden for now; the redeem + entitlement machinery below stays functional. */}
        {donationVisible && !t?.is_supporter ?
          <div className="dx-filters" style={{ marginTop: 12 }}>
            <a className="btn" href={t?.donate_url || "#"} target="_blank" rel="noopener" style={{ textDecoration: "none" }}>Donate from ${t?.min_donation}</a>
          </div> : null}

        <div className="dx-head" style={{ marginTop: 16 }}><b>Device &amp; licence</b></div>
        <div className="mut" style={{ fontSize: 12, lineHeight: 1.7 }}>
          Device id: <code>{t?.device_id ?? "…"}</code>
          <button className="btn ghost sm" style={{ marginLeft: 8 }} onClick={() => { navigator.clipboard?.writeText(t?.device_id || ""); toast("Device id copied."); }}>copy</button>
          <br />Entitlement is bound to this device by a signed licence and verified offline — no account,
          no personal data.
        </div>
        {!t?.is_supporter ?
          <div style={{ marginTop: 10 }}>
            <label className="fld"><span>Redeem a licence</span>
              <textarea value={lic} onChange={(e) => setLic(e.target.value)} style={{ minHeight: 54 }} placeholder='paste your licence JSON here' /></label>
            <div className="dx-filters" style={{ marginTop: 6 }}><button className="btn ghost sm" onClick={redeem}>Redeem</button></div>
          </div> : null}
      </div>
    </div>
  );
}
