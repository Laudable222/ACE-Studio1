"""ACE Studio v2 — FastAPI application factory.

Responsibilities kept deliberately thin:
  - build the app, apply CORS for the Vite dev server,
  - mount every enabled feature module (see app.modules.ENABLED),
  - in production, serve the built React frontend (frontend/dist) with no-store so the
    single-page app is never cached stale.

Run (dev):   uvicorn app.main:app --app-dir backend --port 8766
Run (prod):  build the frontend, then the same command serves it at /.
"""

from __future__ import annotations

import traceback
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import get_settings
from app.core.module import register_modules
from app.modules import ENABLED

settings = get_settings()

app = FastAPI(title=settings.app_name, version=settings.version)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_dev_origin],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def _json_errors(request: Request, exc: Exception):
    """Always return JSON so the frontend can humanise the message — never an HTML 500."""
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"error": f"{type(exc).__name__}: {exc}"})


_loaded = register_modules(app, ENABLED, core={"health", "meta", "session", "data", "knowledge", "research", "generate", "simulate", "evolution", "settings"})
print(f"[ace] mounted modules: {', '.join(_loaded) or '(none)'}")


# ── production: serve the built frontend (if present) ────────────────────────────────
_DIST = settings.repo_root / "frontend" / "dist"
_NO_STORE = {"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"}


@app.get("/api")
def api_root():
    return {"app": settings.app_name, "version": settings.version, "modules": _loaded}


if _DIST.exists():
    # Hash-named assets can cache forever; index.html must not (SPA freshness).
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html", headers=_NO_STORE)
else:
    @app.get("/")
    def _dev_notice():
        return {"message": "Backend up. In dev, open the Vite server at "
                           f"{settings.frontend_dev_origin}. In prod, build the frontend first."}
