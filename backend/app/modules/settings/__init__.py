"""Settings module — API keys, LLM provider/model choice + live key test, and BRAIN login.

Reuses the studio key store (keys) and session manager (session_manager) via the brain
adapter, so v2 shares the exact same secrets file and session the user already has.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.brain import engine  # noqa: F401 — loads the vendored engine + keys into env

import keys as keymgr          # noqa: E402
import session_manager as sm   # noqa: E402
import llm_providers as L      # noqa: E402

PREFIX = "/api/settings"
router = APIRouter()


# ── API keys ─────────────────────────────────────────────────────────────────────────

@router.get("/keys")
def get_keys():
    return {"status": keymgr.status()}


class KeysReq(BaseModel):
    updates: dict


@router.post("/keys")
def set_keys(req: KeysReq):
    return {"status": keymgr.set_keys(req.updates)}


@router.post("/keys/clear")
def clear_keys():
    return {"status": keymgr.set_keys({name: keymgr.CLEAR for name in keymgr.PROVIDERS})}


# ── providers / models ───────────────────────────────────────────────────────────────

@router.get("/providers")
def providers():
    pref = keymgr.get_preferred()
    return {"available": [p.name for p in L.all_available()], "preferred": pref,
            "used": [p.name for p in L.default_chain(pref)]}


class PreferReq(BaseModel):
    preferred: str = ""


@router.post("/providers")
def set_provider(req: PreferReq):
    keymgr.set_preferred(req.preferred)
    pref = keymgr.get_preferred()
    return {"available": [p.name for p in L.all_available()], "preferred": pref,
            "used": [p.name for p in L.default_chain(pref)]}


@router.get("/providers/{provider}/models")
def provider_models(provider: str):
    if provider not in L.PROVIDER_KEY_ENV:
        raise HTTPException(404, f"unknown provider {provider!r}")
    return {"provider": provider, "models": L.list_models(provider),
            "current": L.provider_model(provider), "default": L.provider_default(provider)}


class ModelReq(BaseModel):
    model: str = ""


@router.post("/providers/{provider}/model")
def set_provider_model(provider: str, req: ModelReq):
    if provider not in L.PROVIDER_KEY_ENV:
        raise HTTPException(404, f"unknown provider {provider!r}")
    keymgr.set_provider_model(provider, req.model)
    return {"provider": provider, "current": L.provider_model(provider)}


@router.post("/providers/{provider}/test")
def provider_test(provider: str):
    return L.test_provider(provider)


# ── BRAIN login (biometrics, two steps) ──────────────────────────────────────────────

class LoginReq(BaseModel):
    email: str | None = None
    password: str | None = None


@router.post("/login/begin")
def login_begin(req: LoginReq):
    return sm.begin_login(req.email, req.password)


@router.post("/login/complete")
def login_complete():
    return sm.complete_login()


class TaskRouteReq(BaseModel): provider:str; model:str=""
@router.get("/llm/routes")
def llm_routes():
 from app.core import llm_router
 return {"routes":llm_router.routes()}
@router.post("/llm/routes/{task}")
def set_llm_route(task:str,req:TaskRouteReq):
 from app.core import llm_router
 try:return llm_router.set_route(task,req.provider,req.model)
 except ValueError as e:raise HTTPException(400,str(e))

@router.get("/llm/usage")
def llm_usage():
    from app.core import llm_router
    return llm_router.usage_snapshot()
