import { useEffect, useState } from "react";
import { api } from "../../lib/api";
import { useToast } from "../../lib/toast";

const PROVIDERS: Record<string, string> = {
  anthropic: "Claude (Anthropic)", openai: "OpenAI", gemini: "Gemini", deepseek: "DeepSeek",
  groq: "Groq (free)", huggingface: "Hugging Face", openrouter: "OpenRouter", mistral: "Mistral",
  together: "Together AI", xai: "xAI (Grok)",
};

// Settings: API keys, the active LLM provider + model with a live key test, BRAIN login,
// your simulation tags, and appearance.
export function Settings() {
  const { toast, toastErr } = useToast();
  const [status, setStatus] = useState<Record<string, { set: boolean; hint: string }>>({});
  const [prov, setProv] = useState<{ available: string[]; preferred: string; used: string[] }>({ available: [], preferred: "", used: [] });
  const [keyProv, setKeyProv] = useState("anthropic");
  const [keyVal, setKeyVal] = useState("");
  const [modelProv, setModelProv] = useState("");
  const [models, setModels] = useState<{ models: string[]; current: string }>({ models: [], current: "" });
  const [modelSearch, setModelSearch] = useState("");
  const [modelLoading, setModelLoading] = useState(false);
  const [researchRoute, setResearchRoute] = useState<{ provider: string; model: string }>({ provider: "", model: "" });
  const [researchRouteLoading, setResearchRouteLoading] = useState(false);
  const [testOut, setTestOut] = useState<string>("");
  const [testing, setTesting] = useState(false);
  const [tag, setTag] = useState(() => localStorage.getItem("ace2-tag") || "");
  const [winnerTag, setWinnerTag] = useState(() => localStorage.getItem("ace2-winnertag") || "");
  const [email, setEmail] = useState("");
  const [pass, setPass] = useState("");
  const [loginMsg, setLoginMsg] = useState("");

  const loadKeys = () => api.get<any>("/settings/keys").then((d) => setStatus(d.status || {}));
  const loadProv = () => api.get<any>("/settings/providers").then((d) => {
    // Never trust a degraded/404 response to have the shape we want — default every field
    // so a backend still starting up can't white-screen this whole screen.
    const av: string[] = Array.isArray(d?.available) ? d.available : [];
    const preferred = typeof d?.preferred === "string" ? d.preferred : "";
    setProv({ available: av, preferred, used: Array.isArray(d?.used) ? d.used : [] });

    // Keep the model selector tied to the persisted preferred provider.
    // Previously Gemini won simply because it was available.
    if (preferred && av.includes(preferred)) {
      setModelProv(preferred);
    } else if (av.length) {
      setModelProv((m) => av.includes(m) ? m : av[0]);
    }
  });
  const loadResearchRoute = async () => {
    try {
      const d = await api.get<any>("/settings/llm/routes");
      const r = d?.routes?.research ?? d;
      if (r?.provider) setResearchRoute({ provider: r.provider, model: r.model ?? "" });
    } catch {
      // Keep Settings usable with older backends that do not expose task routes.
    }
  };

  useEffect(() => { loadKeys(); loadProv(); loadResearchRoute(); }, []);
  async function loadModels() {
    if (!modelProv) return;
    setModelLoading(true);
    const providerAtRequest = modelProv;
    try {
      const d = await api.get<any>(`/settings/providers/${providerAtRequest}/models`);
      // Ignore a slower response from an older provider request. Without this guard,
      // the initial Gemini request can finish after OpenRouter has been restored and
      // overwrite the OpenRouter model list.
      if (providerAtRequest !== modelProv) return;
      setModels({
        models: Array.isArray(d?.models) ? d.models : [],
        current: d?.current ?? ""
      });
    } finally {
      if (providerAtRequest === modelProv) setModelLoading(false);
    }
  }
  useEffect(() => {
    setModelSearch("");
    setModels({ models: [], current: "" });
    loadModels();
    /* eslint-disable-next-line */
  }, [modelProv]);

  async function saveKey() {
    if (!keyVal.trim()) return;
    const d = await api.post("/settings/keys", { updates: { [keyProv]: keyVal.trim() } });
    if (d.error) return toastErr(d.error);
    setKeyVal(""); toast(`${PROVIDERS[keyProv]} key saved.`); loadKeys(); loadProv();
  }
  async function setPreferred(p: string) {
    await api.post("/settings/providers", { preferred: p }); loadProv();
  }
  async function setModel(m: string) {
    await api.post(`/settings/providers/${modelProv}/model`, { model: m });
    setModels((s) => ({ ...s, current: m })); toast(`${modelProv} → ${m}`);
  }

  async function saveResearchRoute() {
    if (!researchRoute.provider) return;
    setResearchRouteLoading(true);
    try {
      const r = await api.post<any>("/settings/llm/routes/research", researchRoute);
      if (r?.error) return toastErr(r.error);
      toast(`Research → ${researchRoute.provider}${researchRoute.model ? ` / ${researchRoute.model}` : ""}`);
      await loadResearchRoute();
    } finally {
      setResearchRouteLoading(false);
    }
  }
  async function testKey() {
    setTesting(true); setTestOut("testing…");
    const r = await api.post<any>(`/settings/providers/${modelProv}/test`);
    setTesting(false);
    setTestOut(r.ok ? `✓ works — ${r.model} replied` : `✗ ${r.error}${r.raw ? " · " + r.raw : ""}`);
  }
  function saveTags() {
    localStorage.setItem("ace2-tag", tag); localStorage.setItem("ace2-winnertag", winnerTag);
    toast("Tags saved — used when you simulate.");
  }
  async function beginLogin() {
    setLoginMsg("contacting BRAIN…");
    const r = await api.post<any>("/settings/login/begin", { email: email || null, password: pass || null });
    if (r.error) { setLoginMsg(""); return toastErr(r.error); }
    if (r.state === "biometrics" && r.url) {
      window.open(r.url, "_blank", "noopener");   // pop the biometrics page automatically
      setLoginMsg("Biometrics opened in a new tab — complete it there, then click Finish login.");
    } else {
      setLoginMsg(r.message || r.url || "Open the biometrics link in your browser, then click Finish login.");
    }
  }
  async function completeLogin() {
    const r = await api.post<any>("/settings/login/complete");
    if (r.error) return toastErr(r.error);
    setLoginMsg("Login complete."); toast("Logged in.");
  }

  return (
    <div className="dx-split" style={{ flex: 1, minHeight: 0 }}>
      {/* keys + providers */}
      <div className="panel panel-scroll" style={{ padding: 14 }}>
        <div className="dx-head"><b>LLM providers</b></div>
        <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>The selected provider is the only one used for generation.</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <label className="fld"><span>Active provider</span>
            <select value={prov.preferred} onChange={(e) => setPreferred(e.target.value)}>
              <option value="">auto (first available)</option>
              {prov.available.map((p) => <option key={p}>{p}</option>)}
            </select></label>
          <label className="fld"><span>Model for {modelProv} · {models.models.length} available</span>
            <select value={models.current} onChange={(e) => setModel(e.target.value)}>
              {models.models.filter((m) => !modelSearch || m.toLowerCase().includes(modelSearch.toLowerCase())).map((m) => <option key={m}>{m}</option>)}
            </select></label>
        </div>
        <div className="dx-filters" style={{ marginTop: 8 }}>
          <select value={modelProv} onChange={(e) => setModelProv(e.target.value)} style={{ maxWidth: 150 }}>
            {Object.keys(PROVIDERS).map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <input value={modelSearch} onChange={(e) => setModelSearch(e.target.value)} placeholder="search models…" style={{ flex: 1 }} />
          <button className="btn ghost sm" onClick={loadModels} disabled={modelLoading}>{modelLoading ? <><span className="spin" /> Refreshing…</> : "↻ Refresh models"}</button>
          <button className="btn ghost sm" onClick={testKey} disabled={testing}>{testing ? <><span className="spin" /> Testing…</> : "✓ Test Key"}</button>
        </div>
        {testOut ? <div className="mut" style={{ fontSize: 12, marginTop: 6, color: testOut.startsWith("✓") ? "var(--ok)" : testOut.startsWith("✗") ? "var(--bad)" : "var(--mut)" }}>{testOut}</div> : null}

        <div className="dx-head" style={{ marginTop: 14 }}><b>Research task routing</b></div>
        <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>
          Research/MD analysis uses this persisted task route, independently of the general preferred provider.
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8 }}>
          <label className="fld"><span>Research provider</span>
            <select value={researchRoute.provider}
              onChange={(e) => setResearchRoute({ provider: e.target.value, model: "" })}>
              <option value="">select provider</option>
              {Object.entries(PROVIDERS).map(([p, label]) => <option key={p} value={p}>{label}</option>)}
            </select>
          </label>
          <label className="fld"><span>Research model</span>
            <input value={researchRoute.model}
              onChange={(e) => setResearchRoute((r) => ({ ...r, model: e.target.value }))}
              placeholder="model id (optional)" />
          </label>
          <button className="btn sm" style={{ alignSelf: "end" }}
            onClick={saveResearchRoute}
            disabled={!researchRoute.provider || researchRouteLoading}>
            {researchRouteLoading ? <><span className="spin" /> Saving…</> : "Save research route"}
          </button>
        </div>

        <div className="dx-head" style={{ marginTop: 14 }}><b>API keys</b>
          <span className="mut">{Object.values(status).filter((s) => s.set).length} of {Object.keys(PROVIDERS).length} set</span></div>
        <div className="dx-filters">
          <select value={keyProv} onChange={(e) => setKeyProv(e.target.value)} style={{ maxWidth: 150 }}>
            {Object.entries(PROVIDERS).map(([k, v]) => <option key={k} value={k}>{v}{status[k]?.set ? " ✓" : ""}</option>)}
          </select>
          <input type="password" placeholder="paste key…" value={keyVal} onChange={(e) => setKeyVal(e.target.value)} style={{ flex: 1 }} />
          <button className="btn sm" onClick={saveKey}>Save</button>
        </div>
        <div className="mut" style={{ fontSize: 11, marginTop: 6 }}>Keys are stored locally in ~/secrets/ace_keys.json and never leave your machine.</div>
      </div>

      {/* login + tags + appearance */}
      <div className="panel panel-scroll" style={{ padding: 14 }}>
        <div className="dx-head"><b>BRAIN session</b></div>
        <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>Login is never automatic. Enter your BRAIN details, open the biometrics link, then Finish.</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
          <label className="fld"><span>Email (blank uses saved)</span><input value={email} onChange={(e) => setEmail(e.target.value)} /></label>
          <label className="fld"><span>Password</span><input type="password" value={pass} onChange={(e) => setPass(e.target.value)} /></label>
        </div>
        <div className="dx-filters" style={{ marginTop: 8 }}>
          <button className="btn sm" onClick={beginLogin}>Begin login</button>
          <button className="btn ghost sm" onClick={completeLogin}>Finish login</button>
        </div>
        {loginMsg ? <div className="mut" style={{ fontSize: 12, marginTop: 6, wordBreak: "break-all" }}>{loginMsg}</div> : null}

        <div className="dx-head" style={{ marginTop: 14 }}><b>Your tags</b></div>
        <div className="mut" style={{ fontSize: 12, marginBottom: 8 }}>Applied when you simulate — nothing is hardcoded.</div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 8 }}>
          <label className="fld"><span>Tag every alpha</span><input value={tag} onChange={(e) => setTag(e.target.value)} placeholder="my_news" /></label>
          <label className="fld"><span>Winner tag</span><input value={winnerTag} onChange={(e) => setWinnerTag(e.target.value)} placeholder="my_winner" /></label>
          <button className="btn sm" style={{ alignSelf: "end" }} onClick={saveTags}>Save</button>
        </div>

        <div className="dx-head" style={{ marginTop: 14 }}><b>Appearance</b></div>
        <div className="mut" style={{ fontSize: 12 }}>Use the sun / moon toggle in the top bar to switch between light and dark mode.</div>
      </div>
    </div>
  );
}
