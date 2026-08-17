"""
platform_log.py — a small, thread-safe, in-memory event log for ACE Studio.

Events are categorized so the UI can show them in separate panels:
  session  — login / biometrics / session lifecycle (with timing)
  ace      — WorldQuant BRAIN / ace_lib API calls that failed
  web      — server / HTTP errors (unhandled exceptions, bad requests)
  job      — background jobs (generate / simulate / sweep) milestones + errors

It's a bounded ring buffer (nothing is written to disk), polled by the UI.
"""

from __future__ import annotations

import threading
import time

CATEGORIES = ("session", "ace", "web", "job")
_MAX = 800

_lock = threading.Lock()
_entries: list = []
_seq = 0


def log(category: str, msg, level: str = "info") -> int:
    """Append an event. level is 'info' | 'warn' | 'error'. Returns its id."""
    global _seq
    cat = category if category in CATEGORIES else "web"
    with _lock:
        _seq += 1
        _entries.append({"id": _seq, "t": time.time(), "level": level,
                         "category": cat, "msg": str(msg)[:800]})
        if len(_entries) > _MAX:
            del _entries[:len(_entries) - _MAX]
        return _seq


def info(category, msg):
    return log(category, msg, "info")


def warn(category, msg):
    return log(category, msg, "warn")


def error(category, msg):
    return log(category, msg, "error")


def entries(category: str = None, since: int = 0, limit: int = 400) -> list:
    with _lock:
        rows = [e for e in _entries
                if e["id"] > since and (not category or e["category"] == category)]
        return rows[-limit:]


def last_id() -> int:
    with _lock:
        return _seq


def clear(category: str = None) -> None:
    with _lock:
        if category:
            _entries[:] = [e for e in _entries if e["category"] != category]
        else:
            _entries.clear()
