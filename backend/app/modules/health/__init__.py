"""Health / liveness module — proves the backend is up and reports its version."""

from __future__ import annotations

import time

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter()
_STARTED = time.time()


@router.get("/ping")
def ping():
    s = get_settings()
    return {
        "ok": True,
        "app": s.app_name,
        "version": s.version,
        "uptime_seconds": round(time.time() - _STARTED, 1),
    }
