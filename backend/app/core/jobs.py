"""Minimal background-job registry so long tasks (LLM research, later generation and
simulation) survive a page refresh. Tasks run on daemon threads; the UI polls status.

A task callable receives (progress, should_cancel) where progress(message=…, log=…) reports
status and should_cancel() -> bool lets it stop cooperatively.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict = {}
        self._cancels: dict = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, fn) -> str:
        jid = uuid.uuid4().hex[:12]
        ev = threading.Event()
        job = {"id": jid, "kind": kind, "status": "running", "message": "",
               "total": 0, "done": 0,
               "result": None, "error": None, "trace": None, "created": time.time(), "log": []}
        with self._lock:
            self._jobs[jid] = job
            self._cancels[jid] = ev

        def progress(message=None, log=None, total=None, done=None):
            with self._lock:
                if message is not None:
                    job["message"] = message
                if total is not None:
                    job["total"] = total
                if done is not None:
                    job["done"] = done
                if log is not None:
                    job["log"].append({"t": time.time(), "msg": str(log)[:500]})
                    if len(job["log"]) > 200:
                        del job["log"][:len(job["log"]) - 200]

        def run():
            try:
                res = fn(progress, ev.is_set)
                with self._lock:
                    job["status"] = "cancelled" if ev.is_set() else "done"
                    job["result"] = res
            except Exception as e:  # noqa: BLE001
                with self._lock:
                    job["status"] = "error"
                    job["error"] = str(e) or e.__class__.__name__
                    job["trace"] = traceback.format_exc()

        threading.Thread(target=run, daemon=True).start()
        return jid

    def get(self, jid: str) -> dict:
        with self._lock:
            j = self._jobs.get(jid)
            if not j:
                return {}
            d = dict(j)
            d["log"] = list(j.get("log", []))
            return d

    def list_recent(self, limit: int = 40) -> list:
        """Compact list of jobs (running first, then most recent) for the global job tray."""
        with self._lock:
            items = [{"id": j["id"], "kind": j["kind"], "status": j["status"],
                      "message": j.get("message", ""), "total": j.get("total", 0),
                      "done": j.get("done", 0), "created": j.get("created", 0)}
                     for j in self._jobs.values()]
        items.sort(key=lambda j: (j["status"] != "running", -j["created"]))
        return items[:limit]

    def cancel(self, jid: str) -> bool:
        with self._lock:
            ev = self._cancels.get(jid)
            job = self._jobs.get(jid)
            if not ev or not job or job["status"] != "running":
                return False
            ev.set()
            job["message"] = "cancelling…"
            return True


jobs = JobManager()
