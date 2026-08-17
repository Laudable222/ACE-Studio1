"""Session module — live BRAIN session status and a soft restart.

The engine never logs in from here (headless-safe); logging in stays a Settings action.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.brain import engine

PREFIX = "/api/session"
router = APIRouter()


@router.get("/status")
def status():
    return engine.session_status()


@router.post("/restart")
def restart():
    return engine.session_restart()
