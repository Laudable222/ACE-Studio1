"""Meta module — app-wide metadata the frontend shell needs at boot: the navigation
map (so the sidebar/header render from one source of truth) and build info.

The navigation map lives on the backend deliberately: as modules come online they can
contribute or gate screens, and the tier/entitlement system can later hide or unlock
routes without a frontend change.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter()

# groups the sidebar renders, in order. `ready=False` screens show a "coming soon"
# placeholder until their module lands in a later phase.
NAV = [
    {"group": "Overview", "items": [
        {"id": "command", "path": "/", "label": "Command Center", "icon": "grid", "ready": True},
    ]},
    {"group": "Research", "items": [
        {"id": "data", "path": "/data", "label": "Data Explorer", "icon": "database", "ready": True},
        {"id": "knowledge", "path": "/knowledge", "label": "Knowledge Graph", "icon": "share", "ready": True},
        {"id": "research", "path": "/research", "label": "Research Lab", "icon": "flask", "ready": True},
        {"id": "discovery", "path": "/discovery", "label": "Research Engine", "icon": "sparkles", "ready": True},
        {"id": "submission", "path": "/submission", "label": "Submission Manager", "icon": "send", "ready": True},
        {"id": "evolution", "path": "/evolution", "label": "Alpha Evolution", "icon": "sparkles", "ready": True},
        {"id": "replication", "path": "/replication", "label": "Alpha Replication", "icon": "globe", "ready": True},
        {"id": "strategies", "path": "/strategies", "label": "Strategy Atlas", "icon": "compass", "ready": True},
        {"id": "prompts", "path": "/prompts", "label": "Prompt Library", "icon": "bookmark", "ready": True},
    ]},
    {"group": "Build", "items": [
        {"id": "templates", "path": "/templates", "label": "Template Studio", "icon": "layout", "ready": True},
        {"id": "generate", "path": "/generate", "label": "Generation", "icon": "sparkles", "ready": True},
    ]},
    {"group": "Run", "items": [
        {"id": "simulate", "path": "/simulate", "label": "Simulation", "icon": "play", "ready": True},
        {"id": "super", "path": "/super", "label": "SuperAlpha", "icon": "star", "ready": True},
    ]},
    {"group": "Analyse", "items": [
        {"id": "results", "path": "/results", "label": "Results & Analytics", "icon": "chart", "ready": True},
        {"id": "portfolio", "path": "/portfolio", "label": "Correlation & Portfolio", "icon": "grid2", "ready": True},
        {"id": "operators", "path": "/operators", "label": "Operator Atlas", "icon": "list", "ready": True},
        {"id": "regions", "path": "/regions", "label": "Region & Universe Atlas", "icon": "globe", "ready": True},
    ]},
    {"group": "System", "items": [
        {"id": "tier", "path": "/tier", "label": "Success", "icon": "heart", "ready": True},
        {"id": "settings", "path": "/settings", "label": "Settings", "icon": "settings", "ready": True},
    ]},
]


@router.get("/nav")
def nav():
    return {"nav": NAV}


@router.get("/info")
def info():
    s = get_settings()
    return {"app": s.app_name, "version": s.version}


@router.get("/jobs")
def jobs_list(limit: int = 40):
    """All recent background jobs (running first) for the global job tray."""
    from app.core.jobs import jobs
    return {"jobs": jobs.list_recent(limit)}
