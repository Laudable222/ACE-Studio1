"""A tiny disk-backed LLM response cache. Applied ONLY to idempotent calls (relationship
judging, cross-region rating, prompt naming) where the same input should give the same answer —
never to idea generation, where fresh output is the point. Cuts API cost and speeds up retries.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time

from app.core.config import get_settings

_LOCK = threading.Lock()
_MEM: dict = {}
_LOADED = False
_TTL = 24 * 3600          # a day
_MAX_BYTES = 4_000_000    # keep the on-disk cache modest


def _path():
    return get_settings().data_dir / "llm_cache.json"


def _ensure_loaded():
    global _LOADED
    if _LOADED:
        return
    try:
        _MEM.update(json.loads(_path().read_text()))
    except Exception:
        pass
    _LOADED = True


def _key(prompt: str, n) -> str:
    return hashlib.sha256((f"{n}|" + prompt).encode("utf-8", "ignore")).hexdigest()


def cached_generate_list(task: str, prompt: str, n=None, max_tokens=8000):
    """Cached, idempotent LLM call routed through TaskLLM so token budgets and provider routing
    are enforced. Cache hits do not consume tokens."""
    from app.core.llm_router import TaskLLM
    _ensure_loaded()
    k = _key(f"{task}|{prompt}", n)
    with _LOCK:
        e = _MEM.get(k)
    if e and (time.time() - e.get("t", 0) < _TTL):
        v = e["v"]
        return type("Cached", (), {"expressions": list(v.get("expressions", [])),
                                   "provider": v.get("provider", "cache"), "model": v.get("model", "")})()
    res = TaskLLM(task).generate_list(prompt, n=n, max_tokens=max_tokens)
    try:
        with _LOCK:
            _MEM[k] = {"t": time.time(), "v": {"expressions": list(res.expressions),
                                               "provider": res.provider, "model": res.model}}
            blob = json.dumps(_MEM)
            if len(blob) <= _MAX_BYTES:
                _path().parent.mkdir(parents=True, exist_ok=True)
                _path().write_text(blob)
    except Exception:
        pass
    return res
