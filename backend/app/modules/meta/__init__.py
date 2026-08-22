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
        {"id": "command", "path": "/", "label": "Command Center", "icon": "grid", "ready": True,
         "description": "Best alphas, next actions, and running jobs at a glance — your daily starting point."},
    ]},
    {"group": "Research", "items": [
        {"id": "data", "path": "/data", "label": "Data Explorer", "icon": "database", "ready": True,
         "description": "Fetch BRAIN datasets and their fields into your local catalogue. Everything else in ACE Studio reads from what's fetched here — nothing else queries BRAIN directly for data."},
        {"id": "knowledge", "path": "/knowledge", "label": "Knowledge Graph", "icon": "share", "ready": True,
         "description": "Cross-region dataset/field equivalents, plus the Knowledge Vault — paste tips, rules, and lessons that get surfaced back into research and generation prompts."},
        {"id": "research", "path": "/research", "label": "Research Lab", "icon": "flask", "ready": True,
         "description": "Pick fields manually and generate, or run Autopilot to scan your whole local catalogue and generate across every matched hypothesis with no selection required."},
        {"id": "discovery", "path": "/discovery", "label": "Research Engine", "icon": "sparkles", "ready": True,
         "description": "Turn a research report into hypotheses — including ones it implies but never states outright — map them to real BRAIN fields, and generate candidates per hypothesis."},
        {"id": "submission", "path": "/submission", "label": "Submission Manager", "icon": "send", "ready": True,
         "description": "Track which verified alphas are ready, queued, or submitted to BRAIN, with your own daily submission pace."},
        {"id": "evolution", "path": "/evolution", "label": "Alpha Evolution", "icon": "sparkles", "ready": True,
         "description": "Diagnose why a failed alpha failed, then propose controlled, one-change-at-a-time variants — window, operator, neutralization — building on whichever attempt did best so far."},
        {"id": "replication", "path": "/replication", "label": "Alpha Replication", "icon": "globe", "ready": True,
         "description": "Port a working alpha to a different region, verifying the target fields actually exist first rather than guessing."},
        {"id": "strategies", "path": "/strategies", "label": "Strategy Atlas", "icon": "compass", "ready": True,
         "description": "Browse alpha strategy ideas by category, seeded per-device so what you see diverges from everyone else's."},
        {"id": "prompts", "path": "/prompts", "label": "Prompt Library", "icon": "bookmark", "ready": True,
         "description": "Save and reuse your own generation instructions instead of retyping them every time."},
    ]},
    {"group": "Build", "items": [
        {"id": "templates", "path": "/templates", "label": "Template Studio", "icon": "layout", "ready": True,
         "description": "Write a reusable expression with {field} placeholders, or ask the LLM to suggest templates from your data, then expand it across many real fields at once."},
        {"id": "generate", "path": "/generate", "label": "Generation", "icon": "sparkles", "ready": True,
         "description": "Turn selected fields, a pasted idea, or many pasted datafield descriptions into candidate FastExpr alphas — individually and in combination."},
    ]},
    {"group": "Run", "items": [
        {"id": "simulate", "path": "/simulate", "label": "Simulation", "icon": "play", "ready": True,
         "description": "Run candidates through BRAIN's real simulation queue and judge every result against one explicit gate — sharpe, fitness, turnover, correlation."},
        {"id": "super", "path": "/super", "label": "SuperAlpha", "icon": "star", "ready": True,
         "description": "Combine your own existing BRAIN alphas into higher-order expressions."},
    ]},
    {"group": "Analyse", "items": [
        {"id": "results", "path": "/results", "label": "Results & Analytics", "icon": "chart", "ready": True,
         "description": "Every simulation result, filterable and searchable, with an experiment ledger and pairwise correlation."},
        {"id": "portfolio", "path": "/portfolio", "label": "Correlation & Portfolio", "icon": "grid2", "ready": True,
         "description": "Your simulated alpha book — check production correlation before submitting, since BRAIN judges each submission against everything you already have live."},
        {"id": "operators", "path": "/operators", "label": "Operator Atlas", "icon": "list", "ready": True,
         "description": "Every BRAIN operator's real signature and example, pulled from your own account — the same reference every generation prompt uses."},
        {"id": "regions", "path": "/regions", "label": "Region & Universe Atlas", "icon": "globe", "ready": True,
         "description": "Which regions, delays, and universes are available, and what's already catalogued locally for each."},
    ]},
    {"group": "System", "items": [
        {"id": "tier", "path": "/tier", "label": "Success", "icon": "heart", "ready": True,
         "description": "Your verified successes, and the device-bound entitlement tied to demonstrating real results — not a paywall."},
        {"id": "settings", "path": "/settings", "label": "Settings", "icon": "settings", "ready": True,
         "description": "BRAIN login, LLM providers and API keys, and which model handles which task."},
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
