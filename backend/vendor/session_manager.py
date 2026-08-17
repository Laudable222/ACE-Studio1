"""
session_manager.py — Single source of truth for the WorldQuant BRAIN session.

The rules enforced here (and nowhere else):

  1. session.pkl is the ONE persisted session. It is loaded on startup and the
     manager holds it in memory for the life of the process.
  2. The session is probed for validity with retries, so a transient network
     error is never mistaken for an expired token.
  3. Login happens ONLY when the pkl is missing or the token is genuinely
     expired. A valid (or merely unreachable) session is never re-logged-in.
  4. An in-process single-flight lock + atomic pkl writes mean concurrent worker
     threads can never trigger duplicate logins or corrupt the pkl.
  5. Workers call require_session(): it returns the current session and NEVER
     logs in. If the token has genuinely expired mid-run it raises
     SessionExpired so the caller can pause and re-auth (biometrics needs a
     human) — it does not silently start a new session.

Notebook usage (replaces the old try/except session block):

    import session_manager as sm
    s = sm.get_session()          # loads pkl; logs in only if missing/expired
    print(sm.status_line())

Worker usage (never logs in):

    s = sm.require_session()      # raises SessionExpired if the token is dead
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import tempfile
import threading
import time
from urllib.parse import urljoin

import ace_lib as ace

logger = logging.getLogger("ace.session")

SESSION_FILE = os.environ.get("ACE_SESSION_FILE", "session.pkl")

# Refresh proactively when a valid session has less than this many seconds left,
# but only on the login-capable path (get_session). Workers never refresh.
MIN_REMAINING_SECONDS = int(os.environ.get("ACE_MIN_REMAINING_SECONDS", "600"))

# How hard to probe before deciding a 0/blip is real. A genuinely expired token
# returns 401 immediately; only ambiguous (network/5xx) responses are retried.
PROBE_RETRIES = int(os.environ.get("ACE_PROBE_RETRIES", "3"))
PROBE_BACKOFF = float(os.environ.get("ACE_PROBE_BACKOFF", "3.0"))
PROBE_TIMEOUT = float(os.environ.get("ACE_PROBE_TIMEOUT", "30.0"))

# Probe states
VALID = "VALID"        # authenticated; seconds-remaining is meaningful
EXPIRED = "EXPIRED"    # server said unauthorized — a real re-login is required
UNKNOWN = "UNKNOWN"    # could not determine (network/5xx) — DO NOT re-login


class SessionExpired(Exception):
    """Raised on the worker path when the token is genuinely expired."""


_login_lock = threading.Lock()          # single-flight: only one login at a time
_state = {"session": None}


# ─────────────────────────────────────────────────────────────────────────────
# Formatting
# ─────────────────────────────────────────────────────────────────────────────

def format_time(seconds) -> str:
    seconds = max(0, int(seconds or 0))
    hours, rem = divmod(seconds, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


# ─────────────────────────────────────────────────────────────────────────────
# Probe — the one place that distinguishes "expired" from "unreachable"
# ─────────────────────────────────────────────────────────────────────────────

def _probe(s) -> tuple[str, int]:
    """
    Return (state, seconds_remaining).

    A 401 means the token is genuinely dead -> EXPIRED (login is warranted).
    A 2xx with a token.expiry means VALID. Anything else (timeout, connection
    error, 5xx) is treated as UNKNOWN after PROBE_RETRIES, so a network blip
    never forces a re-login.
    """
    url = ace.brain_api_url + "/authentication"
    for attempt in range(PROBE_RETRIES):
        try:
            r = s.get(url, timeout=PROBE_TIMEOUT)
        except Exception as e:  # noqa: BLE001 - any transport error is "unknown"
            logger.debug("Session probe attempt %d transport error: %s", attempt + 1, e)
            time.sleep(PROBE_BACKOFF * (attempt + 1))
            continue

        if r.status_code == 401:
            return (EXPIRED, 0)

        if r.status_code // 100 == 2:
            try:
                expiry = int(r.json()["token"]["expiry"])
                return (VALID, expiry)
            except Exception as e:  # noqa: BLE001
                logger.debug("Session probe 2xx but unparseable body: %s", e)
                time.sleep(PROBE_BACKOFF * (attempt + 1))
                continue

        # 5xx / unexpected status — treat as transient
        logger.debug("Session probe attempt %d got status %s", attempt + 1, r.status_code)
        time.sleep(PROBE_BACKOFF * (attempt + 1))

    return (UNKNOWN, 0)


# ─────────────────────────────────────────────────────────────────────────────
# pkl I/O (atomic)
# ─────────────────────────────────────────────────────────────────────────────

def _load_pkl():
    if not os.path.exists(SESSION_FILE) or os.path.getsize(SESSION_FILE) == 0:
        return None
    try:
        with open(SESSION_FILE, "rb") as f:
            return pickle.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read %s (%s); treating as no session.", SESSION_FILE, e)
        return None


def _save_pkl(s) -> None:
    directory = os.path.dirname(os.path.abspath(SESSION_FILE)) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".pkl.tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            pickle.dump(s, f)
        os.replace(tmp, SESSION_FILE)  # atomic on the same filesystem
        try:
            os.chmod(SESSION_FILE, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _login_and_store():
    """Perform an interactive login and persist it. Caller must hold _login_lock."""
    logger.info("Starting a new BRAIN session (login).")
    s = ace.start_session()
    _save_pkl(s)
    _state["session"] = s
    logger.info("New session stored to %s.", SESSION_FILE)
    return s


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_session(login: bool = True):
    """
    Return a usable session.

    - If a valid session already exists (in memory or in session.pkl), it is
      returned as-is. A login is NEVER performed while a valid session exists.
    - If the probe is UNKNOWN (network trouble), the existing session is reused
      rather than risking a needless login.
    - A login happens only when the session is missing or the token has
      genuinely expired (server 401), and only when login=True. With login=False
      an expired/missing session raises SessionExpired.
    """
    with _login_lock:
        s = _state["session"] or _load_pkl()

        if s is not None:
            state, remaining = _probe(s)
            if state == VALID:
                _state["session"] = s
                if login and remaining < MIN_REMAINING_SECONDS:
                    logger.info(
                        "Session valid but low (%s left); refreshing before work starts.",
                        format_time(remaining),
                    )
                    return _login_and_store()
                logger.info("Reusing existing session (%s remaining).", format_time(remaining))
                return s
            if state == UNKNOWN:
                logger.warning(
                    "Session state UNKNOWN (network?). Reusing existing session; NOT logging in."
                )
                _state["session"] = s
                return s
            logger.info("Session token is genuinely expired.")

        if not login:
            raise SessionExpired(
                "No valid session available and login is disabled on this path."
            )
        return _login_and_store()


def require_session():
    """
    Worker-facing accessor. Returns the current session and NEVER logs in.
    Raises SessionExpired if the token is genuinely dead so the caller can pause
    the run and trigger a human re-auth instead of silently starting a session.
    """
    s = _state["session"] or _load_pkl()
    if s is None:
        raise SessionExpired("No session loaded. Call get_session() first.")
    state, _ = _probe(s)
    if state == EXPIRED:
        raise SessionExpired("Session token expired mid-run. Pause and re-authenticate.")
    # VALID or UNKNOWN -> keep working with what we have.
    _state["session"] = s
    return s


def remaining_seconds() -> int:
    """Best-effort seconds remaining on the current session (0 if unknown/expired)."""
    s = _state["session"] or _load_pkl()
    if s is None:
        return 0
    state, remaining = _probe(s)
    return remaining if state == VALID else 0


def status_line() -> str:
    s = _state["session"] or _load_pkl()
    if s is None:
        return "Session: none (login required)"
    state, remaining = _probe(s)
    if state == VALID:
        return f"Session: valid, {format_time(remaining)} remaining"
    if state == UNKNOWN:
        return "Session: present but unreachable (network); reusing without login"
    return "Session: expired (re-auth required)"


def ui_session() -> dict:
    """Lenient status for the UI: as long as the pkl holds a session that is not
    definitively expired (server 401), report it as present. Network blips
    (UNKNOWN) keep the last-known remaining time instead of dropping to 'none'."""
    s = _state["session"] or _load_pkl()
    if s is None:
        return {"ok": False, "state": "none", "remaining": 0}
    state, remaining = _probe(s)
    if state == VALID:
        _state["session"] = s
        _state["last_remaining"] = remaining
        return {"ok": True, "state": "valid", "remaining": remaining}
    if state == UNKNOWN:
        _state["session"] = s
        return {"ok": True, "state": "unknown", "remaining": _state.get("last_remaining", 0)}
    return {"ok": False, "state": "expired", "remaining": 0}


# ─────────────────────────────────────────────────────────────────────────────
# Web-friendly biometrics login (two steps, no blocking input())
#
# Mirrors ace_lib.start_session's persona branch but split so a UI can:
#   1) begin_login() -> get the biometrics URL to open in the browser
#   2) (user completes biometrics)
#   3) complete_login() -> POST confirms (201) and the pkl is written
# ─────────────────────────────────────────────────────────────────────────────

# BRAIN credentials are intentionally memory-only by default. Persisting the user's password
# as plaintext on disk is unsafe. Environment variables remain supported for unattended local
# setups, but the normal UI login does not write credentials to a file.
_pending: dict = {"session": None, "location": None}


def _have_credentials() -> bool:
    return bool(os.environ.get("BRAIN_CREDENTIAL_EMAIL") and os.environ.get("BRAIN_CREDENTIAL_PASSWORD"))


def _write_credentials(email: str, password: str) -> None:
    # Kept as a compatibility hook. Normal ACE UI login never calls this.
    raise RuntimeError("Persistent BRAIN password storage is disabled for security. Use the login form or environment variables.")


def begin_login(email: str = None, password: str = None) -> dict:
    """
    Start authentication. Returns one of:
      {"state": "done", "remaining": int}
      {"state": "biometrics", "url": "<open this in the browser>"}
      {"state": "error", "message": str}
    """
    import platform_log as plog
    with _login_lock:
        # Prefer credentials supplied directly by the UI. They are used only for this
        # authentication attempt and are never persisted. Environment credentials remain
        # available for explicit unattended setups.
        if not ((email and password) or _have_credentials()):
            plog.warn("session", "Login attempted with no email/password.")
            return {"state": "error",
                    "message": "Enter your BRAIN email and password, then click Begin login."}
        plog.info("session", "Authenticating with BRAIN… (this can take 10–30s)")
        t0 = time.time()
        try:
            s = ace.SingleSession()
            if email and password:
                s.auth = (email, password)
            else:
                s.auth = ace.get_credentials()
            r = s.post(ace.brain_api_url + "/authentication")
        except Exception as e:  # noqa: BLE001
            plog.error("session", f"Authentication request failed: {e}. Check your internet and try again.")
            return {"state": "error",
                    "message": f"Could not reach BRAIN: {e}. Check your internet connection and try again."}
        dt = time.time() - t0
        (plog.warn if dt > 20 else plog.info)("session", f"BRAIN responded in {dt:.0f}s (status {r.status_code}).")

        if r.status_code == 201 or r.status_code // 100 == 2:
            _save_pkl(s)
            _state["session"] = s
            plog.info("session", "Logged in directly — session saved.")
            return {"state": "done", "remaining": ace.check_session_timeout(s)}

        if r.status_code == 401:
            if r.headers.get("WWW-Authenticate") == "persona":
                location = urljoin(r.url, r.headers["Location"])
                _pending["session"] = s
                _pending["location"] = location
                plog.info("session", "Biometrics required — open the link, finish it, then click Finish login.")
                return {"state": "biometrics", "url": location}
            plog.error("session", "Incorrect email or password.")
            return {"state": "error", "message": "Incorrect email or password."}

        plog.error("session", f"Unexpected auth status {r.status_code}.")
        return {"state": "error", "message": f"Unexpected status {r.status_code}. Try again or Restart."}


def complete_login() -> dict:
    """
    Confirm a biometrics login. Call after the user finishes biometrics in the
    browser (the notebook equivalent of pressing Enter). Returns:
      {"state": "done", "remaining": int}   -> pkl written
      {"state": "pending", "message": str}  -> not complete yet, retry
      {"state": "error", "message": str}
    """
    import platform_log as plog
    with _login_lock:
        s = _pending["session"]
        location = _pending["location"]
        if s is None or location is None:
            plog.warn("session", "Finish login clicked but no login is in progress — click Begin login first.")
            return {"state": "error", "message": "No login in progress. Click Begin login first."}
        try:
            r = s.post(location)
        except Exception as e:  # noqa: BLE001
            plog.error("session", f"Biometrics confirmation failed: {e}")
            return {"state": "error", "message": f"Confirmation request failed: {e}"}
        if r.status_code == 201:
            _save_pkl(s)
            _state["session"] = s
            _pending["session"] = None
            _pending["location"] = None
            plog.info("session", "Biometrics confirmed — session saved.")
            return {"state": "done", "remaining": ace.check_session_timeout(s)}
        plog.warn("session", "Biometrics not finished yet — complete it in the browser, then click Finish again.")
        return {"state": "pending",
                "message": "Biometrics not complete yet. Finish in the browser, then retry."}


def soft_reset() -> dict:
    """Drop the in-memory session and any half-finished login, then reload from the
    pkl on disk. Fixes a stuck/stale session WITHOUT restarting the process."""
    import platform_log as plog
    with _login_lock:
        _pending["session"] = None
        _pending["location"] = None
        _state["session"] = None
        _state["session"] = _load_pkl()
    plog.info("session", "Soft restart — in-memory session cleared and reloaded from disk.")
    return ui_session()
