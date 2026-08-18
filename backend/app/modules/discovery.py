from __future__ import annotations
import json
from fastapi import APIRouter, File, Form, UploadFile, HTTPException
from sqlalchemy import select
from pydantic import BaseModel
from app.core.jobs import jobs
from app.db.base import SessionLocal
from app.db import models as M
from app.discovery import service

PREFIX="/api/discovery"
router=APIRouter()
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 2_000_000

class AnalyzeReq(BaseModel):
    document_id:int
    use_llm:bool=True
    auto_map:bool=True
    region:str='IND'
    delay:int=1
    universe:str='TOP3000'
    instrument:str='EQUITY' 

@router.post('/documents')
async def upload_document(file:UploadFile=File(...)):
    data=await file.read()
    if len(data) > MAX_DOCUMENT_BYTES:
        raise HTTPException(413, f'Research document is too large. Maximum size is {MAX_DOCUMENT_BYTES // (1024*1024)} MB.')
    name=file.filename or 'research.md'
    try: text=data.decode('utf-8-sig')
    except UnicodeDecodeError: raise HTTPException(400,'This endpoint expects a UTF-8 Markdown/text file.')
    if not text.strip(): raise HTTPException(400,'The Markdown file is empty.')
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(413, f'Research document is too large. Maximum text length is {MAX_TEXT_CHARS:,} characters.')
    return service.ingest_document(text,name,'markdown')

class TextReq(BaseModel):
    title:str=''
    content:str
    source:str='markdown'

@router.post('/documents/text')
def text_document(req:TextReq):
    if not req.content.strip(): raise HTTPException(400,'Research text is empty.')
    if len(req.content) > MAX_TEXT_CHARS:
        raise HTTPException(413, f'Research text is too large. Maximum length is {MAX_TEXT_CHARS:,} characters.')
    return service.ingest_document(req.content,req.title,req.source)

@router.get('/documents')
def documents(limit:int=50): return {'documents':service.list_documents(limit)}

@router.post('/analyze')
def analyze(req:AnalyzeReq):
    def task(progress, should_cancel):
        docs=service.list_documents(1000)
        doc=next((d for d in docs if d['id']==req.document_id),None)
        if not doc: raise RuntimeError('research document not found')
        progress(message='extracting research claims, mechanisms and hypotheses…')
        # Retrieve content directly to avoid returning it through the list endpoint.
        from app.db.base import SessionLocal
        from app.db import models as M
        with SessionLocal() as db: row=db.get(M.ResearchDocument,req.document_id); content=row.content
        extraction,provider,model=service.extract_research(content,use_llm=req.use_llm)
        extraction['provenance']={'document_id':req.document_id,'provider':provider,'model':model}
        service.save_extraction(req.document_id,extraction)
        progress(message=f"{len(extraction.get('hypotheses',[]))} hypotheses extracted")
        mapped={}
        if req.auto_map and extraction.get('hypotheses'):
            progress(message='scanning BRAIN catalogue and selecting fields automatically…')
            mapped=service.auto_map_fields(extraction,region=req.region,delay=req.delay,universe=req.universe,instrument=req.instrument,top_k=16)
        return {'document_id':req.document_id,'extraction':extraction,'provider':provider,'model':model,'field_mapping':mapped}
    return {'job_id':jobs.submit('research-analysis',task)}

class MapReq(BaseModel):
    extraction:dict
    fields:list[dict]=[]
    top_k:int=12
    region:str='IND'
    delay:int=1
    universe:str='TOP3000'
    instrument:str='EQUITY'

@router.post('/map-fields')
def map_fields(req:MapReq):
    # Manual mode remains available for users who deliberately select fields.
    return {'matches':service.map_fields(req.extraction,req.fields,max(1,min(req.top_k,50)))}

@router.post('/map-fields-auto')
def map_fields_auto(req:MapReq):
    # Autonomous mode scans the BRAIN catalogue. No manual dataset/field selection is required.
    return service.auto_map_fields(req.extraction,region=req.region,delay=req.delay,universe=req.universe,
                                   instrument=req.instrument,top_k=max(1,min(req.top_k,50)))

class ExperimentReq(BaseModel):
    name:str=''
    region:str='IND'
    delay:int=1
    universe:str='TOP3000'
    research_ids:list[int]=[]
    hypothesis:dict={}
    field_ids:list[dict]=[]
    notes:str=''

@router.post('/experiments')
def experiment(req:ExperimentReq): return {'id':service.create_experiment(req.name,req.region,req.delay,req.universe,req.research_ids,req.hypothesis,req.field_ids,req.notes)}

@router.get('/experiments')
def experiments(limit:int=50): return {'experiments':service.list_experiments(limit)}

class DnaReq(BaseModel):
    expression:str
    region:str=''
    categories:dict[str,str]={}

@router.post('/dna')
def dna(req:DnaReq):
    if not req.expression.strip(): raise HTTPException(400,'Expression is empty.')
    d=service.alpha_dna(req.expression,req.region,req.categories); d['id']=service.save_dna(d); return d

@router.get('/dna')
def dnas(limit:int=100,region:str=''): return {'alphas':service.dna_list(limit,region)}

@router.get('/field-intelligence')
def field_intelligence(region:str='',limit:int=50): return {'fields':service.field_intelligence(region,limit)}

class MutateReq(BaseModel):
    expressions:list[str]
    fields:list[dict]=[]
    max_operators:int=4
    max_results:int=60

@router.post('/mutate')
def mutate(req:MutateReq):
    return {'mutations':service.mutate_expressions(req.expressions,req.fields,req.max_operators,req.max_results)}

class ExperimentGenerateReq(BaseModel):
    experiment_id:int
    n:int=12
    max_operators:int=4
    repair_rounds:int=2

@router.post('/experiments/generate')
def experiment_generate(req:ExperimentGenerateReq):
    from app.generation import service as gen
    from app.knowledge import service as ks
    with SessionLocal() as db:
        row=db.get(M.Experiment,req.experiment_id)
        if not row: raise HTTPException(404,'experiment not found')
        hypothesis=service._safe_json(row.hypothesis_json,{})
        field_refs=service._safe_json(row.field_ids_json,[])
    if not field_refs:
        raise HTTPException(400,'This experiment has no BRAIN fields saved. Map the hypothesis to fields and create it again.')
    # Resolved from the experiment's own saved field references — not from whatever happens
    # to be on screen right now — so generation always uses exactly what was mapped for THIS
    # hypothesis, even if the Research Engine screen has since moved on to another report.
    fields=ks.fields_by_refs(field_refs,row.region,row.delay)
    if not fields:
        raise HTTPException(400,f'The BRAIN fields saved for this experiment are not in the local catalogue for '
                                 f'{row.region} D{row.delay}. Fetch datasets for this region/delay in Data Explorer, then create the experiment again.')
    categories=ks.dataset_categories([f['dataset_id'] for f in fields],row.region,row.delay)
    prompt=(f"Test this research hypothesis: {hypothesis.get('statement','')}\n"
            f"Economic mechanism: {hypothesis.get('mechanism','')}\n"
            f"Expected sign: {hypothesis.get('expected_sign',hypothesis.get('sign',''))}\n"
            f"Horizon: {hypothesis.get('horizon','')}\n"
            "Do not copy a formula from the research report. Construct multiple distinct expressions that test the same mechanism. "
            "Prefer structurally different constructions and avoid cosmetic duplicates.")
    def task(progress,should_cancel):
        out=gen.run_generation(mode='multi',prompt=prompt,region=row.region,delay=row.delay,instrument='EQUITY',universe=row.universe,
                               dataset_names=[],fields=fields,categories=categories,max_operators=req.max_operators,n=req.n,
                               repair_rounds=req.repair_rounds,region_note='',progress=progress,raw_prompt=False)
        with SessionLocal() as db:
            r=db.get(M.Experiment,req.experiment_id); exprs=service._safe_json(r.expression_json,[])
            exprs=list(dict.fromkeys(exprs+out.get('valid',[]))); r.expression_json=json.dumps(exprs); r.status='generated'; db.commit()
        for e in out.get('valid',[]):
            try: service.save_dna(service.alpha_dna(e,row.region,categories))
            except Exception: pass
        out['experiment_id']=req.experiment_id
        return out
    return {'job_id':jobs.submit('experiment-generation',task)}

@router.get('/failures')
def failures(limit:int=100,region:str=''):
    with SessionLocal() as db:
        q=select(M.ResearchFailure).order_by(M.ResearchFailure.created_at.desc()).limit(limit)
        if region: q=q.where(M.ResearchFailure.region==region)
        rows=db.scalars(q).all()
        return {'failures':[{'id':r.id,'expression':r.expression,'region':r.region,'reason':r.reason,'details':service._safe_json(r.details_json,{}),'created_at':r.created_at,'experiment_id':r.experiment_id} for r in rows]}

@router.get('/robustness')
def robustness(expression:str):
    from statistics import mean, pstdev
    with SessionLocal() as db:
        rows=db.scalars(select(M.SimResult).where(M.SimResult.expression==expression)).all()
    vals=[abs(r.sharpe) for r in rows if r.sharpe is not None]
    passes=sum(1 for r in rows if r.passed_gate)
    if not vals: return {'expression':expression,'runs':0,'score':0.0,'pass_rate':0.0}
    avg=mean(vals); stability=max(0.0,1.0-(pstdev(vals)/(avg+1e-9))) if len(vals)>1 else 1.0
    return {'expression':expression,'runs':len(rows),'avg_abs_sharpe':round(avg,4),'sharpe_stability':round(stability,4),'pass_rate':round(passes/len(rows),4),'score':round(0.5*stability+0.5*(passes/len(rows)),4)}
