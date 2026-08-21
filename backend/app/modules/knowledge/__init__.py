"""Knowledge module — the cross-region intelligence surface.

Read-only queries over the knowledge DB (which is populated automatically by the data
module on every fetch). `on_startup` ensures the schema exists.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.core.jobs import jobs
from app.db.base import init_db
from app.knowledge import service

PREFIX = "/api/knowledge"
router = APIRouter()


def on_startup():
    init_db()


@router.get("/overview")
def overview():
    return service.overview()


@router.get("/dataset/{dataset_id}/twins")
def dataset_twins(dataset_id: str, region: str = "USA", delay: int = 1):
    return service.dataset_twins(dataset_id, region, delay)


@router.get("/dataset/{dataset_id}/similar")
def similar(dataset_id: str, region: str = "USA", delay: int = 1):
    return {"dataset_id": dataset_id, "similar": service.similar_datasets(dataset_id, region, delay)}


@router.get("/field/{field_id}/equivalents")
def field_equivalents(field_id: str, region: str = "USA", delay: int = 1):
    return service.field_equivalents(field_id, region, delay)


class JudgeReq(BaseModel):
    base_id: str
    region: str = "USA"
    delay: int = 1
    candidates: list[dict] = []


@router.post("/cross-region")
def cross_region():
    """AI analysis of which shared cross-region concepts are most worth exploiting."""
    def task(progress, should_cancel):
        return service.analyze_cross_region(min_regions=2, progress=progress)
    return {"job_id": jobs.submit("knowledge-xregion", task)}


@router.post("/judge")
def judge(req: JudgeReq):
    """Run an AI relevance check over lexical candidates (background — it calls an LLM)."""
    def task(progress, should_cancel):
        return service.judge_relationships(req.base_id, req.region, req.delay, req.candidates, progress)
    return {"job_id": jobs.submit("knowledge-judge", task)}


@router.get("/jobs/{jid}")
def job(jid: str):
    j = jobs.get(jid)
    if not j:
        raise HTTPException(404, "no such job")
    return j


class MemoryReq(BaseModel):
    type:str="tip"; title:str=""; content:str; region:str=""; dataset:str=""; field:str=""; operator:str=""; tags:list[str]=[]; confidence:str="unverified"; source:str="user"; evidence:dict={}
@router.get("/memory")
def memories(limit:int=100,type:str="",region:str="",q:str=""): return {"items":service.list_memories(limit,type,region,q)}
@router.post("/memory")
def memory(req:MemoryReq):
    if not req.content.strip(): raise HTTPException(400,"Memory content cannot be empty.")
    return service.add_memory(req.type,req.title or req.content[:80],req.content,req.region,req.dataset,req.field,req.operator,req.tags,req.confidence,req.source,req.evidence)
@router.get("/memory/relevant")
def relevant(q:str,region:str="",field:str="",dataset:str="",limit:int=8): return {"items":service.retrieve_memories(q,region,field,dataset,limit)}
@router.delete("/memory/{item_id}")
def delete_memory(item_id:int):
    if not service.delete_memory(item_id): raise HTTPException(404,"memory not found")
    return {"ok":True}
