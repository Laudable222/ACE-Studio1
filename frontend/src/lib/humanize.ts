// Turn any raw/technical error into ONE plain sentence with a next step. Ported from v1
// so no user ever sees a stack trace, HTTP code, or exception class. Provider-key and
// BRAIN-limit cases are matched before the generic 401/429 ones.
export function humanizeError(raw: unknown): string {
  const s = String(raw ?? "").trim();
  if (!s) return "Something went wrong. Please try again.";
  const low = s.toLowerCase();
  const has = (...xs: string[]) => xs.some((x) => low.includes(x));

  if (has("network error", "failed to fetch", "is the ace studio server", "is the server running"))
    return "Can’t reach the app. Make sure the ACE Studio server is still running, then try again.";
  if (has("timeout", "timed out")) return "That took too long and timed out. Please try again in a moment.";
  if (has("no llm providers", "add an api key", "no providers"))
    return "No AI provider is set up yet. Add an API key in Settings (for example Gemini or Claude).";
  if (has("invalid api key", "invalid_api_key", "incorrect api key", "api key"))
    return "The AI provider rejected your API key. Re-check it in Settings, or use the “Test key” button there.";
  if (has("insufficient", "out of credit", "quota", "billing", "402"))
    return "Your AI provider is out of credit or quota. Add credit, or switch provider in Settings.";
  if (has("concurrent_simulation", "concurrent-simulation"))
    return "BRAIN is already running the maximum number of simulations. Lower concurrency or wait.";
  if (has("no active brain session", "no valid session", "session expired", "401", "unauthorized", " authentication"))
    return "Your BRAIN session isn’t active. Open Settings and log in, then try again.";
  if (has("rate limit", "429", "too many requests"))
    return "The AI provider is busy (rate-limited). Wait a moment and try again, or switch providers.";
  if (has("unknown variable"))
    return "A selection expression uses an attribute the platform doesn’t recognise.";
  if (has("transient error", "try again in a moment", "could not load"))
    return "BRAIN had a brief hiccup. Please try again in a moment.";
  if (has("keyerror", "valueerror", "typeerror", "attributeerror", "nonetype", "traceback", "unexpected character"))
    return "The app hit an unexpected internal error. Please try again; if it keeps happening, use Support.";
  if (has("internal server error") || /^\s*(server error|500|502|503)\b/.test(low))
    return "The server hit an error. Please try again; if it persists, use Support.";
  return s.split("\n")[0].replace(/^[A-Za-z_.]*(Error|Exception):\s*/, "").trim() ||
    "Something went wrong. Please try again.";
}
