"""
keys.py — Persistent API-key store for ACE Studio.

Keys are saved to ~/secrets/ace_keys.json (outside the repo) and loaded into the
process environment on startup, so llm_providers picks them up. The UI can view
(masked) and update them; they persist until changed.
"""

from __future__ import annotations

import json
import os

KEYS_FILE = os.path.join(os.path.expanduser("~"), "secrets", "ace_keys.json")

# provider name -> environment variable
PROVIDERS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "huggingface": "HF_TOKEN",
    "openrouter": "OPENROUTER_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "xai": "XAI_API_KEY",
}
EXTRA = {}
CLEAR = "__clear__"

# provider name (as used by llm_providers) -> its model env var
MODEL_ENV = {
    "claude": "CLAUDE_MODEL",
    "gemini": "GEMINI_MODEL",
    "openai": "OPENAI_MODEL",
    "deepseek": "DEEPSEEK_MODEL",
    "groq": "GROQ_MODEL",
    "huggingface": "HUGGINGFACE_MODEL",
    "openrouter": "OPENROUTER_MODEL",
    "mistral": "MISTRAL_MODEL",
    "together": "TOGETHER_MODEL",
    "xai": "XAI_MODEL",
}


def _read() -> dict:
    try:
        with open(KEYS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _write(d: dict) -> None:
    os.makedirs(os.path.dirname(KEYS_FILE), exist_ok=True)
    with open(KEYS_FILE, "w") as f:
        json.dump(d, f, indent=2)
    try:
        os.chmod(KEYS_FILE, 0o600)
        os.chmod(os.path.dirname(KEYS_FILE), 0o700)
    except Exception:
        pass


def load_into_env() -> None:
    """Apply saved keys (from ~/secrets/ace_keys.json) to os.environ."""
    d = _read()
    for env in list(PROVIDERS.values()) + list(EXTRA.values()):
        v = d.get(env)
        if v:
            os.environ[env] = v
    # Per-provider model choices, stored as model_<provider> -> <PROVIDER>_MODEL env.
    for prov, env in MODEL_ENV.items():
        v = d.get("model_" + prov)
        if v:
            os.environ[env] = v


def get_provider_model(provider: str) -> str:
    """The model the user pinned for a provider ('' means use the provider default)."""
    return _read().get("model_" + provider, "") or ""


def set_provider_model(provider: str, model: str) -> str:
    d = _read()
    env = MODEL_ENV.get(provider)
    model = (model or "").strip()
    if model:
        d["model_" + provider] = model
        if env:
            os.environ[env] = model
    else:
        d.pop("model_" + provider, None)
        if env:
            os.environ.pop(env, None)
    _write(d)
    return get_provider_model(provider)


def status() -> dict:
    d = _read()
    out = {}
    for name, env in {**PROVIDERS, **EXTRA}.items():
        val = os.environ.get(env) or d.get(env) or ""
        out[name] = {"set": bool(val), "hint": ("…" + val[-4:]) if len(val) >= 4 else ("set" if val else ""), "env": env}
    return out


def set_keys(updates: dict) -> dict:
    """updates: {provider_name: value}. Empty string = leave unchanged;
    the sentinel '__clear__' removes the key."""
    d = _read()
    for name, val in (updates or {}).items():
        env = PROVIDERS.get(name) or EXTRA.get(name) or name
        val = (val or "").strip()
        if val == "":
            continue
        if val == CLEAR:
            d.pop(env, None)
            os.environ.pop(env, None)
            continue
        d[env] = val
        os.environ[env] = val
    _write(d)
    return status()


def get_preferred() -> str:
    """The provider the user pinned as primary ('' means auto: first available)."""
    return _read().get("preferred", "") or ""


def set_preferred(name: str) -> str:
    d = _read()
    if name:
        d["preferred"] = name
    else:
        d.pop("preferred", None)
    _write(d)
    return get_preferred()
