"""Operator Lab module — the DB-backed operator reference the Operator Atlas edits and the
LLM prompts consume. Read the list, (re)seed from the account operators, edit a row's example
or notes."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.db.base import init_db
from app.operators import service

PREFIX = "/api/operators"
router = APIRouter()


def on_startup():
    init_db()
    service.migrate_schema()   # upgrade operator_ref to the composite (name, scope) key if needed


@router.get("/list")
def list_ops(scope: str = ""):
    return {"operators": service.list_ops(scope)}


@router.post("/seed")
def seed():
    return service.seed()


class CheckReq(BaseModel):
    expression: str


@router.post("/check")
def check(req: CheckReq):
    """Validate a sample expression (operator sandbox) — syntax/arity/keyword/semantics, no field
    restriction."""
    from app.generation import service as gen
    return gen.sandbox_validate(req.expression)


class UpdateReq(BaseModel):
    example: str | None = None
    notes: str | None = None


@router.post("/op/{name}")
def update(name: str, req: UpdateReq):
    return service.update_op(name, req.example, req.notes)
