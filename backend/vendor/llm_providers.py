"""
llm_providers.py — Provider-agnostic LLM layer for alpha generation.

One interface, several backends, all keyed from environment variables (never
config.json). Providers self-report availability, so a chain silently skips any
backend whose SDK or API key is missing.

  ClaudeProvider     official `anthropic` SDK, structured output via messages.parse
                     — replaces fragile regex extraction
  OpenAICompat       `openai` SDK against an OpenAI-compatible endpoint
                     (OpenAI, DeepSeek, Groq, Hugging Face router)
  GeminiProvider     `google-genai`, JSON response mode

Each provider's model is selectable per key; the active model comes from
<PROVIDER>_MODEL (CLAUDE_MODEL / GEMINI_MODEL / OPENAI_MODEL / DEEPSEEK_MODEL /
HUGGINGFACE_MODEL), otherwise a sensible default. list_models() fetches the exact
models a key can use.

MultiLLM tries providers in order with retry/backoff (a fallback chain), and
returns provenance (which model produced the output).

The repair loop (repair / generate_valid) ties generation to validator.py: any
expression that fails validation is sent back to the LLM with its exact error,
re-validated, up to N rounds. Only clean expressions come out.

Environment variables:
  ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, DEEPSEEK_API_KEY,
  GROQ_API_KEY, HF_TOKEN
"""

from __future__ import annotations

import importlib.util
import json
import os
import random
import re
import time
from dataclasses import dataclass, field

MARKERS = (
    "rank(", "ts_", "vec_", "group_", "if_else", "trade_when",
    "signed_power", "purify", "hump", "scale", "winsorize", "densify",
)


class LLMError(Exception):
    pass


def friendly_error(exc) -> str:
    """Turn a raw provider exception into a short, plain-English reason."""
    s = str(exc)
    low = s.lower()
    if "402" in s or "insufficient balance" in low or "insufficient_quota" in low or "billing" in low:
        return "insufficient balance / quota — add credit or switch provider"
    if ("401" in s or "invalid api key" in low or "unauthorized" in low
            or "authentication" in low or "invalid_api_key" in low or "no auth" in low):
        return "invalid or missing API key"
    if "403" in s or "forbidden" in low or "permission" in low:
        return "access denied (the key lacks permission)"
    if "429" in s or "rate limit" in low or "rate_limit" in low or "too many requests" in low:
        return "rate limited — wait a moment or switch provider"
    if "timeout" in low or "timed out" in low:
        return "timed out (slow or unreachable endpoint)"
    if "404" in s or ("model" in low and ("not found" in low or "does not exist" in low
                                          or "no longer available" in low)):
        return "model not available for your key — pick another model in Settings"
    if "no expressions" in low:
        return "returned no usable expressions"
    first = s.strip().splitlines()[0] if s.strip() else exc.__class__.__name__
    return first[:160]


def _is_fatal(exc) -> bool:
    """Errors where retrying the same provider is pointless (auth / balance)."""
    low = str(exc).lower()
    return any(k in low for k in ("402", "insufficient balance", "401", "invalid api key",
                                  "unauthorized", "authentication", "403", "forbidden",
                                  "invalid_api_key", "billing"))


def _has_module(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _canonical(expr: str) -> str:
    """Formatting-insensitive key: collapse whitespace so 'ts_rank(x, 20)' and
    'ts_rank(x,20)' are recognised as the same expression."""
    return re.sub(r"\s+", "", expr or "").lower()


def _dedup(seq):
    """De-duplicate while preserving order and the first-seen original spelling,
    using a whitespace-insensitive key so near-duplicates collapse."""
    out, seen = [], set()
    for s in seq:
        if not s or not s.strip():
            continue
        s = s.strip()
        k = _canonical(s)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def extract_list(text) -> list:
    """Best-effort parse of an LLM response into a list of expression strings."""
    if isinstance(text, list):
        return _dedup(str(x) for x in text)
    text = re.sub(r"```[\w]*", "", str(text)).strip()

    # 1) whole body is JSON (array, or object with an 'expressions' field)
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            obj = obj.get("expressions") or obj.get("alphas") or []
        if isinstance(obj, list):
            return _dedup(str(e) for e in obj)
    except json.JSONDecodeError:
        pass

    # 2) first bracketed array anywhere in the text
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                return _dedup(str(e) for e in arr)
        except json.JSONDecodeError:
            pass

    # 3) fall back to marker-bearing lines
    lines = [l.strip().strip(",").strip('"').strip("'") for l in text.splitlines() if l.strip()]
    return _dedup(l for l in lines if any(k in l for k in MARKERS))


# ─────────────────────────────────────────────────────────────────────────────
# Providers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class LLMResult:
    expressions: list
    provider: str
    model: str


class Provider:
    name = "base"
    model = ""

    def available(self) -> bool:
        raise NotImplementedError

    def generate_list(self, prompt: str, *, n=None, max_tokens=8000) -> list:
        raise NotImplementedError


class ClaudeProvider(Provider):
    name = "claude"

    def __init__(self, model=None, use_thinking=True, max_tokens=8000):
        self.model = model or provider_model("claude")
        self.use_thinking = use_thinking
        self.max_tokens = max_tokens

    def available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY")) and _has_module("anthropic")

    def generate_list(self, prompt, *, n=None, max_tokens=None) -> list:
        import anthropic
        client = anthropic.Anthropic()
        max_tokens = max_tokens or self.max_tokens

        # Preferred: guaranteed-valid JSON via structured output.
        try:
            from pydantic import BaseModel

            class _ExprList(BaseModel):
                expressions: list[str]

            resp = client.messages.parse(
                model=self.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
                output_format=_ExprList,
            )
            out = getattr(resp, "parsed_output", None)
            if out is not None and out.expressions:
                return _dedup(out.expressions)
        except Exception:
            pass  # fall through to a plain completion

        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if self.use_thinking:
            kwargs["thinking"] = {"type": "adaptive"}
        resp = client.messages.create(**kwargs)
        text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
        return extract_list(text)


class OpenAICompat(Provider):
    """An OpenAI-compatible endpoint (OpenAI, DeepSeek, Groq, Hugging Face router)."""

    def __init__(self, name, model, base_url=None, key_env=None, default_key=None,
                 structured=False, json_mode=True, max_tokens=8000):
        self.name = name
        self.model = model
        self.base_url = base_url
        self.key_env = key_env
        self.default_key = default_key
        self.structured = structured   # response_format: json_schema (strict)
        self.json_mode = json_mode     # response_format: json_object
        self.max_tokens = max_tokens

    def available(self) -> bool:
        if not _has_module("openai"):
            return False
        if self.key_env:
            return bool(os.environ.get(self.key_env))
        return True

    def generate_list(self, prompt, *, n=None, max_tokens=None) -> list:
        from openai import OpenAI
        api_key = (os.environ.get(self.key_env) if self.key_env else None) or self.default_key
        # Fail fast so a slow/unreachable provider doesn't stall the fallback chain.
        client = OpenAI(base_url=self.base_url, api_key=api_key, timeout=30.0, max_retries=1)
        kwargs = dict(
            model=self.model,
            max_tokens=max_tokens or self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        if self.structured:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "expressions",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "properties": {
                            "expressions": {"type": "array", "items": {"type": "string"}}
                        },
                        "required": ["expressions"],
                        "additionalProperties": False,
                    },
                },
            }
        elif self.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            # Newer OpenAI models (gpt-5.x, o-series) rejected `max_tokens` in favour of
            # `max_completion_tokens`, and some reject a custom temperature. Retry once
            # with the newer parameter name so pinning a current model still works — this
            # is a genuine API contract change, not a bad key.
            low = str(e).lower()
            if "max_tokens" in low and ("unsupported" in low or "not supported" in low
                                        or "max_completion_tokens" in low):
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens", None)
                resp = client.chat.completions.create(**kwargs)
            else:
                raise
        return extract_list(resp.choices[0].message.content)


    def generate_json(self, prompt, *, max_tokens=None) -> str:
        """Return raw assistant content for structured research analysis."""
        from openai import OpenAI
        api_key = (os.environ.get(self.key_env) if self.key_env else None) or self.default_key
        client = OpenAI(base_url=self.base_url, api_key=api_key, timeout=45.0, max_retries=1)
        kwargs = dict(model=self.model, max_tokens=max_tokens or self.max_tokens,
                      messages=[{"role": "user", "content": prompt}],
                      response_format={"type": "json_object"})
        try:
            resp = client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            low = str(e).lower()
            if "max_tokens" in low and ("unsupported" in low or "not supported" in low or "max_completion_tokens" in low):
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens", None)
                try:
                    resp = client.chat.completions.create(**kwargs)
                except Exception as e2:  # noqa: BLE001
                    low2 = str(e2).lower()
                    if "response_format" in low2 or "json_object" in low2 or "structured" in low2:
                        kwargs.pop("response_format", None)
                        resp = client.chat.completions.create(**kwargs)
                    else:
                        raise
            elif "response_format" in low or "json_object" in low or "structured" in low:
                kwargs.pop("response_format", None)
                resp = client.chat.completions.create(**kwargs)
            else:
                raise
        content = getattr(resp.choices[0].message, "content", None)
        if isinstance(content, list):
            content = "".join(str(x.get("text", "")) if isinstance(x, dict) else str(x) for x in content)
        content = str(content or "").strip()
        if not content:
            raise LLMError("research provider returned empty content")
        return content


# ─────────────────────────────────────────────────────────────────────────────
# Per-provider model catalog. For each provider: the default model, the key's env
# var, and a small "known" list (cheapest/most-capable-first) used to order the
# LIVE list fetched from the user's key and as a fallback when that fetch fails.
# `gemini-2.5-flash` was dropped as the Gemini default (no longer offered to new
# API users).
# ─────────────────────────────────────────────────────────────────────────────

PROVIDER_KEY_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "huggingface": "HF_TOKEN",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "xai": "XAI_API_KEY",
}
PROVIDER_MODEL_ENV = {p: f"{p.upper()}_MODEL" for p in PROVIDER_KEY_ENV}
# CLAUDE_MODEL, GEMINI_MODEL, OPENAI_MODEL, DEEPSEEK_MODEL, GROQ_MODEL, HUGGINGFACE_MODEL,
# OPENROUTER_MODEL, MISTRAL_MODEL, TOGETHER_MODEL, XAI_MODEL

PROVIDER_DEFAULT_MODEL = {
    "claude": "claude-opus-4-8",
    "gemini": "gemini-2.5-flash-lite",
    "openai": "gpt-4o-mini",
    "deepseek": "deepseek-chat",
    "groq": "llama-3.3-70b-versatile",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
    "openrouter": "openai/gpt-4o-mini",
    "mistral": "mistral-large-latest",
    "together": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
    "xai": "grok-2-latest",
}
# Fallback lists only — used when a live query can't run (no key / offline). The live
# listing from the user's own key always wins, so newer models appear automatically.
PROVIDER_KNOWN_MODELS = {
    "claude": ["claude-haiku-4-5-20251001", "claude-sonnet-5", "claude-opus-4-8"],
    "gemini": ["gemini-2.5-flash-lite", "gemini-3.1-flash-lite", "gemini-2.5-flash",
               "gemini-3-flash-preview", "gemini-3.5-flash", "gemini-2.5-pro"],
    "openai": ["gpt-5.1", "gpt-5.1-mini", "gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4.1-mini",
               "gpt-4o", "gpt-4o-mini", "o4-mini"],
    "deepseek": ["deepseek-chat", "deepseek-reasoner"],
    "groq": ["llama-3.1-8b-instant", "llama-3.3-70b-versatile"],
    "huggingface": ["meta-llama/Llama-3.3-70B-Instruct", "Qwen/Qwen2.5-72B-Instruct",
                    "deepseek-ai/DeepSeek-V3-0324", "mistralai/Mistral-7B-Instruct-v0.3"],
    "openrouter": ["openai/gpt-4o-mini", "openai/gpt-4.1-mini", "anthropic/claude-sonnet-4",
                   "google/gemini-2.5-flash", "deepseek/deepseek-chat",
                   "meta-llama/llama-3.3-70b-instruct"],
    "mistral": ["mistral-large-latest", "mistral-small-latest", "open-mistral-nemo"],
    "together": ["meta-llama/Llama-3.3-70B-Instruct-Turbo", "Qwen/Qwen2.5-72B-Instruct-Turbo",
                 "deepseek-ai/DeepSeek-V3"],
    "xai": ["grok-2-latest", "grok-beta"],
}
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
HF_BASE_URL = "https://router.huggingface.co/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
TOGETHER_BASE_URL = "https://api.together.xyz/v1"
XAI_BASE_URL = "https://api.x.ai/v1"

# provider -> OpenAI-compatible base URL (None = real OpenAI). Drives both the live
# model listing and the provider construction below.
OPENAI_COMPAT_BASE = {
    "openai": None,
    "deepseek": DEEPSEEK_BASE_URL,
    "groq": GROQ_BASE_URL,
    "huggingface": HF_BASE_URL,
    "openrouter": OPENROUTER_BASE_URL,
    "mistral": MISTRAL_BASE_URL,
    "together": TOGETHER_BASE_URL,
    "xai": XAI_BASE_URL,
}

# Backwards-compatible aliases (used elsewhere / by older callers).
GEMINI_DEFAULT_MODEL = PROVIDER_DEFAULT_MODEL["gemini"]


def provider_default(provider: str) -> str:
    return PROVIDER_DEFAULT_MODEL.get(provider, "")


def provider_model(provider: str) -> str:
    """Active model for a provider — the user's choice via <PROVIDER>_MODEL, else default."""
    env = PROVIDER_MODEL_ENV.get(provider, "")
    return (os.environ.get(env) if env else None) or provider_default(provider)


def gemini_model() -> str:            # back-compat
    return provider_model("gemini")


def _filter_model_ids(ids, keep=None, cap=150) -> list:
    drop = ("embedding", "whisper", "tts", "audio", "image", "moderation", "dall-e",
            "rerank", "guard", "vision-ocr")
    out, seen = [], set()
    for i in ids:
        i = str(i or "").strip()
        if not i or i in seen:
            continue
        low = i.lower()
        if any(d in low for d in drop):
            continue
        if keep and not any(k in low for k in keep):
            continue
        seen.add(i)
        out.append(i)
    return out[:cap]


def _list_openai_http(base_url, key, cap=200) -> list:
    """List models straight over HTTP from any OpenAI-compatible /models endpoint.
    Used when the `openai` SDK isn't installed (or its call fails) so the picker still
    shows what the user's key can actually reach instead of a stale hardcoded list."""
    if not key:
        return []
    import urllib.request
    url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}",
                                               "User-Agent": "ace-studio"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return []
    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [(m.get("id") if isinstance(m, dict) else str(m)) for m in items][:cap]


def _list_openai_compat(base_url, key, keep=None, cap=150) -> list:
    """Model ids for an OpenAI-compatible provider: SDK first, plain HTTP as a
    fallback, so a missing `openai` package never hides models the user has."""
    if not key:
        return []
    ids = []
    if _has_module("openai"):
        try:
            from openai import OpenAI
            client = OpenAI(base_url=base_url, api_key=key, timeout=15.0, max_retries=1)
            ids = [m.id for m in client.models.list().data]
        except Exception:
            ids = []
    if not ids:
        ids = _list_openai_http(base_url, key)
    return _filter_model_ids(ids, keep, cap)



def _list_openrouter(key=None, cap=1000) -> list:
    """OpenRouter exposes a provider-independent catalogue. Prefer the live public
    catalogue so new models appear without a code release; when a key is available,
    try the user-filtered catalogue first."""
    import urllib.request
    urls = []
    if key:
        urls.append("https://openrouter.ai/api/v1/models/user?output_modalities=text&sort=newest")
    urls.append("https://openrouter.ai/api/v1/models?output_modalities=text&sort=newest")
    for url in urls:
        try:
            headers = {"User-Agent": "ACE-Studio"}
            if key: headers["Authorization"] = f"Bearer {key}"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as r:
                data=json.loads(r.read().decode())
            items=data.get("data",[]) if isinstance(data,dict) else []
            ids=[]
            for m in items:
                mid=m.get("id") if isinstance(m,dict) else str(m)
                if mid: ids.append(mid)
            filtered=_filter_model_ids(ids, cap=cap)
            if filtered: return filtered
        except Exception:
            continue
    return []

def _list_claude(key) -> list:
    if not key or not _has_module("anthropic"):
        return []
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key)
        return [m.id for m in client.models.list(limit=100).data if "claude" in m.id]
    except Exception:
        return []


def _list_gemini(key) -> list:
    if not key or not _has_module("google.genai"):
        return []
    try:
        from google import genai
        client = genai.Client(api_key=key)
        out = []
        for m in client.models.list():
            actions = (getattr(m, "supported_actions", None)
                       or getattr(m, "supported_generation_methods", None) or [])
            if actions and "generateContent" not in actions:
                continue
            mid = (getattr(m, "name", "") or "").replace("models/", "")
            if mid and "gemini" in mid and "embedding" not in mid and mid not in out:
                out.append(mid)
        return out
    except Exception:
        return []


def list_models(provider: str, key=None) -> list:
    """Model IDs the user's key can actually use for this provider (live), ordered
    known-first; falls back to the known list when no key or the live query fails."""
    key = key or os.environ.get(PROVIDER_KEY_ENV.get(provider, ""), "")
    known = PROVIDER_KNOWN_MODELS.get(provider, [])
    if provider == "gemini":
        live = _list_gemini(key)
    elif provider == "claude":
        live = _list_claude(key)
    elif provider == "openrouter":
        live = _list_openrouter(key, cap=1000)
    elif provider == "openai":
        # keep chat-capable families only; 'gpt' matches gpt-4o/4.1/5/5.1/… as they ship
        live = _list_openai_compat(None, key, keep=("gpt", "o1", "o3", "o4", "o5", "chatgpt"))
    elif provider in OPENAI_COMPAT_BASE:
        live = _list_openai_compat(OPENAI_COMPAT_BASE[provider], key)
    else:
        live = []
    if not live:
        return list(known)
    if provider == "openrouter":
        return live
    ordered = [m for m in known if m in live]
    ordered += [m for m in live if m not in ordered]
    return ordered


def list_gemini_models(api_key=None) -> list:   # back-compat
    return list_models("gemini", api_key)


class GeminiProvider(Provider):
    name = "gemini"

    def __init__(self, model=None, key_env="GEMINI_API_KEY"):
        self.model = model or provider_model("gemini")
        self.key_env = key_env

    def available(self) -> bool:
        return bool(os.environ.get(self.key_env)) and _has_module("google.genai")

    def generate_list(self, prompt, *, n=None, max_tokens=None) -> list:
        from google import genai
        client = genai.Client(api_key=os.environ[self.key_env])
        resp = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        return extract_list(getattr(resp, "text", "") or "")


# -- provider factories --------------------------------------------------------

def openai_provider(model=None):
    return OpenAICompat("openai", model or provider_model("openai"),
                        key_env="OPENAI_API_KEY", structured=True)


def deepseek_provider(model=None):
    # DeepSeek is OpenAI-compatible; deepseek-chat supports JSON mode.
    return OpenAICompat("deepseek", model or provider_model("deepseek"),
                        base_url=DEEPSEEK_BASE_URL, key_env="DEEPSEEK_API_KEY", json_mode=True)


def groq_provider(model=None):
    return OpenAICompat("groq", model or provider_model("groq"),
                        base_url=GROQ_BASE_URL, key_env="GROQ_API_KEY", json_mode=True)


def huggingface_provider(model=None):
    # Hugging Face Inference Providers router is OpenAI-compatible. JSON mode is
    # left off since support varies by the underlying model/provider — extract_list
    # parses the array out of a plain response instead.
    return OpenAICompat("huggingface", model or provider_model("huggingface"),
                        base_url=HF_BASE_URL, key_env="HF_TOKEN", json_mode=False)


def openrouter_provider(model=None):
    # OpenRouter proxies many vendors behind one OpenAI-compatible API.
    return OpenAICompat("openrouter", model or provider_model("openrouter"),
                        base_url=OPENROUTER_BASE_URL, key_env="OPENROUTER_API_KEY", json_mode=False)


def mistral_provider(model=None):
    return OpenAICompat("mistral", model or provider_model("mistral"),
                        base_url=MISTRAL_BASE_URL, key_env="MISTRAL_API_KEY", json_mode=False)


def together_provider(model=None):
    return OpenAICompat("together", model or provider_model("together"),
                        base_url=TOGETHER_BASE_URL, key_env="TOGETHER_API_KEY", json_mode=False)


def xai_provider(model=None):
    return OpenAICompat("xai", model or provider_model("xai"),
                        base_url=XAI_BASE_URL, key_env="XAI_API_KEY", json_mode=False)


def _all_candidates():
    return [
        ClaudeProvider(),
        GeminiProvider(),
        openai_provider(),
        deepseek_provider(),
        groq_provider(),
        huggingface_provider(),
        openrouter_provider(),
        mistral_provider(),
        together_provider(),
        xai_provider(),
    ]


def all_available():
    """Every provider that has a key configured. Use this to populate the UI picker —
    NOT for generation (see default_chain)."""
    return [p for p in _all_candidates() if p.available()]


def get_provider(name):
    """The provider object for a name (with the user's pinned model), or None."""
    for p in _all_candidates():
        if p.name == name:
            return p
    return None


def test_provider(name) -> dict:
    """Make ONE tiny real call to a provider so the user can see, verbatim, whether
    the failure is their key (the endpoint says 401/invalid) or something on our end.
    Returns {ok, provider, model, sample?, error?, raw?}. The `raw` field carries the
    provider's own first error line, unmodified, so an 'invalid API key' can be proven
    to originate at the vendor rather than in this app."""
    p = get_provider(name)
    if p is None:
        return {"ok": False, "provider": name, "error": f"unknown provider {name!r}"}
    if not _has_module("openai") and isinstance(p, OpenAICompat):
        return {"ok": False, "provider": name, "model": getattr(p, "model", ""),
                "error": "the `openai` python package isn't installed on this machine"}
    if not p.available():
        return {"ok": False, "provider": name, "model": getattr(p, "model", ""),
                "error": "no API key configured for this provider (add it in Settings)"}
    try:
        out = p.generate_list(
            'Reply with a JSON array containing exactly one string: ["ok"]. '
            "Return ONLY the JSON array, nothing else.",
            n=1, max_tokens=50)
        return {"ok": True, "provider": name, "model": p.model,
                "sample": (out[0] if out else "")[:80]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "provider": name, "model": getattr(p, "model", ""),
                "error": friendly_error(e), "raw": str(e).strip().splitlines()[0][:220]}


def default_chain(preferred=None):
    """Providers used for GENERATION.

    When the user has pinned a provider, ONLY that provider is used — no silent
    fallback to another vendor (which would spend a different key than intended).
    If the pinned provider has no key configured, fall back to whatever is available
    so the app still works.
    """
    avail = all_available()
    if preferred:
        only = [p for p in avail if p.name == preferred]
        if only:
            return only
    return avail


# ─────────────────────────────────────────────────────────────────────────────
# MultiLLM — fallback chain with retry/backoff
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MultiLLM:
    providers: list
    retries: int = 2
    base_delay: float = 2.0
    max_delay: float = 20.0

    def available_providers(self):
        return [p for p in self.providers if p.available()]

    def generate_list(self, prompt, *, n=None, max_tokens=8000) -> LLMResult:
        usable = self.available_providers()
        if not usable:
            raise LLMError(
                "No LLM providers available. Set an API key (ANTHROPIC_API_KEY / "
                "GEMINI_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY / GROQ_API_KEY / HF_TOKEN)."
            )
        reasons = {}   # provider name -> friendly reason it failed
        for p in usable:
            err = None
            for attempt in range(self.retries + 1):
                try:
                    exprs = p.generate_list(prompt, n=n, max_tokens=max_tokens)
                    if exprs:
                        return LLMResult(exprs, p.name, p.model)
                    err = LLMError("returned no expressions")
                    break
                except Exception as e:  # noqa: BLE001
                    err = e
                    if _is_fatal(e):
                        break   # auth/balance errors won't fix themselves — move on
                    delay = min(self.max_delay, self.base_delay * (2 ** attempt)) + random.uniform(0, 0.5)
                    print(f"  [{p.name}] attempt {attempt + 1} failed: {e} (retry in {delay:.1f}s)")
                    time.sleep(delay)
            reasons[p.name] = friendly_error(err) if err else "unknown error"
            print(f"  [{p.name}] gave up: {reasons[p.name]}")

        lines = "\n".join(f"  • {name}: {r}" for name, r in reasons.items())
        raise LLMError(
            "Alpha generation failed — every configured LLM provider errored:\n" + lines +
            "\n\nFix one of the above (add credit, correct the API key, or switch the active "
            "provider/model in Settings) and try again."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Repair loop — generation ⇄ validation
# ─────────────────────────────────────────────────────────────────────────────

def build_repair_prompt(rejected, validator, base_context="") -> str:
    groups = ", ".join(sorted(validator.valid_groups))
    lines = []
    for i, (expr, issues) in enumerate(rejected, 1):
        errs = "; ".join(f"{iss.code}: {iss.message}" for iss in issues)
        lines.append(f'{i}. "{expr}"  ->  {errs}')
    body = "\n".join(lines)
    return (
        "You are fixing invalid WorldQuant BRAIN FastExpr alpha expressions.\n"
        "Each expression below failed automated validation. Rewrite EACH one to fix\n"
        "ONLY the stated problem while preserving its economic idea. Hard rules:\n"
        "- Exactly ONE raw datafield per expression.\n"
        "- Window/lookback/days are POSITIONAL bare integers: ts_rank(x, 20) — never ts_rank(x, d=20).\n"
        "- Groups are POSITIONAL too: group_zscore(x, industry) — never group_zscore(x, group=industry).\n"
        "- Do NOT attach a keyword attribute to an operator that doesn't define it: hump/k/std/constant/\n"
        "  dense/factor are valid ONLY on the specific operators whose signature lists them (e.g. hump= is\n"
        "  for hump(...), never for ts_rank/ts_delta). If UNKNOWN_KWARG or ARITY is reported, use the\n"
        "  operator strictly per its real signature.\n"
        f"- Use only real operators and valid groups ({groups}).\n"
        "- Reduce VECTOR fields with a vec_* operator before other operators; never use vec_* on MATRIX fields.\n"
        f"{base_context}\n"
        "Failures:\n"
        f"{body}\n\n"
        "Return ONLY a JSON array of corrected expression strings, one per failure, in order."
    )


def repair(multi: MultiLLM, validator, rejected, max_rounds=2, base_context=""):
    """Send failed expressions back to the LLM with their errors; re-validate.
    Returns (fixed_valid_list, still_rejected)."""
    fixed, still = [], list(rejected)
    for _ in range(max_rounds):
        if not still:
            break
        prompt = build_repair_prompt(still, validator, base_context)
        try:
            res = multi.generate_list(prompt, n=len(still))
        except LLMError as e:
            print(f"  repair aborted: {e}")
            break
        valid, still = validator.partition(res.expressions)
        fixed.extend(valid)
    return _dedup(fixed), still


def generate_valid(multi: MultiLLM, validator, prompt, *, repair_rounds=2, base_context=""):
    """Generate, validate, and repair in one call.
    Returns dict(valid, rejected, provider, model, report)."""
    res = multi.generate_list(prompt)
    valid, rejected = validator.partition(res.expressions)
    if rejected and repair_rounds:
        fixed, rejected = repair(multi, validator, rejected, repair_rounds, base_context)
        valid = _dedup(valid + fixed)
    return {
        "valid": valid,
        "rejected": rejected,
        "provider": res.provider,
        "model": res.model,
        "report": {
            "generated": len(res.expressions),
            "valid": len(valid),
            "rejected": len(rejected),
        },
    }
