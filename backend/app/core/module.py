"""The module contract.

A *module* is a self-contained feature: it owns a FastAPI router and (later) its own
service, schema and DB models. Adding a feature means adding a module folder and listing
it in `app.modules.ENABLED` — never editing a monolith.

Each module package must expose a `router: APIRouter`. Optionally it may expose:
  - `PREFIX: str`   (defaults to "/api/<module-name>")
  - `TAGS: list`    (OpenAPI tags)
  - `on_startup()`  (called once at app startup)
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Callable, Optional

from fastapi import APIRouter, FastAPI


@dataclass
class LoadedModule:
    name: str
    router: APIRouter
    prefix: str
    on_startup: Optional[Callable[[], None]]


def load_module(name: str) -> LoadedModule:
    """Import `app.modules.<name>` and read its router + optional metadata."""
    mod = importlib.import_module(f"app.modules.{name}")
    router = getattr(mod, "router", None)
    if router is None:
        raise RuntimeError(f"module {name!r} does not expose a `router`")
    prefix = getattr(mod, "PREFIX", f"/api/{name}")
    return LoadedModule(name=name, router=router, prefix=prefix,
                        on_startup=getattr(mod, "on_startup", None))


def register_modules(app: FastAPI, names: list[str], core: set[str] | None = None) -> list[str]:
    """Mount every enabled module's router and wire its startup hook. Returns the list of
    successfully mounted module names (a failing module is skipped, not fatal, so one bad
    module never takes the whole app down during development)."""
    loaded: list[str] = []
    core = core or set()
    for name in names:
        try:
            lm = load_module(name)
        except Exception as e:  # noqa: BLE001
            if name in core:
                raise RuntimeError(f"Core ACE module {name!r} failed to load: {e}") from e
            app.logger.warning("skipping module %r: %s", name, e) if hasattr(app, "logger") else None
            print(f"[modules] skipping optional module {name!r}: {e}")
            continue
        app.include_router(lm.router, prefix=lm.prefix, tags=[name])
        if lm.on_startup:
            # Run the hook now (during app construction). Our hooks — e.g. init_db — are
            # idempotent and have no ordering needs, so this is simpler and more robust
            # than the deprecated startup-event machinery.
            try:
                lm.on_startup()
            except Exception as e:  # noqa: BLE001
                print(f"[modules] {name} on_startup failed: {e}")
        loaded.append(name)
    return loaded
