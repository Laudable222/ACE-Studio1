from __future__ import annotations
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.brain import engine
from app.brain.engine import SessionExpired
from app.knowledge import service as ks
PREFIX="/api/data"
router=APIRouter()
class FetchReq(BaseModel):
 region:str="IND"; delay:int=1; universe:str="TOP1000"; instrument:str="EQUITY"; theme:bool=False; coverage_min:float=0.0; value_min:float=0.0; category:str=""
class FieldsReq(BaseModel):
 dataset_ids:list[str]; region:str="IND"; delay:int=1; universe:str="TOP1000"; instrument:str="EQUITY"; data_type:str="ALL"; search:str=""
@router.get("/status")
def status():
 try:
  s=engine.session_status(); return {"session":s,"ready":bool(s.get("ok"))}
 except Exception as e: return {"session":{"ok":False,"status":"offline"},"ready":False,"error":str(e)}
@router.get("/options")
def options(refresh:bool=False):
 try:return engine.get_options(refresh=refresh)
 except Exception as e:raise HTTPException(400,str(e))
@router.post("/datasets")
def datasets(req:FetchReq):
 try:
  eff_region, eff_delay, eff_universe = engine.valid_combo(req.instrument, req.region, req.delay, req.universe)
  rows=engine.get_datasets(req.region,req.universe,req.delay,req.instrument,req.theme,req.coverage_min,req.value_min,req.category); ks.ingest_datasets(eff_region,eff_delay,req.instrument,eff_universe,rows); return {"rows":rows,"count":len(rows),"requested":{"region":req.region,"delay":req.delay,"universe":req.universe},"effective":{"region":eff_region,"delay":eff_delay,"universe":eff_universe}}
 except SessionExpired as e:raise HTTPException(401,str(e))
 except Exception as e:raise HTTPException(400,str(e))
@router.post("/fields")
def fields(req:FieldsReq):
 try:
  eff_region, eff_delay, eff_universe = engine.valid_combo(req.instrument, req.region, req.delay, req.universe)
  df,raw=engine.fetch_fields(req.dataset_ids,req.region,req.universe,req.delay,req.instrument,req.data_type,req.search); rows=engine._json_safe(df.to_dict(orient="records")) if df is not None else []; ks.ingest_fields(eff_region,eff_delay,req.dataset_ids,rows); return {"rows":rows,"count":len(rows),"raw_count":raw,"requested":{"region":req.region,"delay":req.delay,"universe":req.universe},"effective":{"region":eff_region,"delay":eff_delay,"universe":eff_universe}}
 except SessionExpired as e:raise HTTPException(401,str(e))
 except Exception as e:raise HTTPException(400,str(e))
@router.get("/catalogue")
def catalogue(region:str="",dataset:str="",field:str="",limit:int=500):
 from sqlalchemy import select
 from app.db.base import SessionLocal
 from app.db import models as M
 with SessionLocal() as db:
  q=select(M.Dataset).order_by(M.Dataset.region,M.Dataset.dataset_id).limit(max(1,min(limit,2000)))
  if region:q=q.where(M.Dataset.region==region)
  if dataset:q=q.where(M.Dataset.dataset_id==dataset)
  ds=db.scalars(q).all(); fq=select(M.Field).order_by(M.Field.region,M.Field.field_id).limit(max(1,min(limit,5000)))
  if region:fq=fq.where(M.Field.region==region)
  if field:fq=fq.where(M.Field.field_id==field)
  fs=db.scalars(fq).all()
 return {"datasets":[{"id":d.dataset_id,"region":d.region,"delay":d.delay,"universe":d.universe,"name":d.name,"description":d.description,"category":d.category,"coverage":d.coverage,"value_score":d.value_score,"alpha_count":d.alpha_count} for d in ds],"fields":[{"id":f.field_id,"dataset_id":f.dataset_id,"region":f.region,"delay":f.delay,"type":f.type,"description":f.description,"alpha_count":f.alpha_count,"is_virgin":f.is_virgin} for f in fs]}
