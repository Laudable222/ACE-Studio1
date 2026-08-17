from __future__ import annotations
import os
import threading
import time
_BUDGET_LOCK = threading.Lock()

DEFAULT_TASKS={"research":"openrouter","alpha_generation":"openrouter","critique":"openrouter","coding":"openrouter","bulk":"openrouter","simulation_analysis":"openrouter"}
def routes():
 import keys
 import llm_providers as L
 d=keys._read(); out={}
 for task,default in DEFAULT_TASKS.items():
  provider=d.get("task_provider_"+task,default); model=d.get("task_model_"+task,"")
  canonical = "claude" if provider == "anthropic" else provider
  env=L.PROVIDER_KEY_ENV.get(canonical, "")
  out[task]={"provider":canonical,"model":model,"configured":bool(env and os.environ.get(env))}
 return out
def set_route(task,provider,model=""):
 import keys,llm_providers as L
 if task not in DEFAULT_TASKS:raise ValueError("unknown LLM task")
 if provider == "anthropic": provider = "claude"
 if provider not in L.PROVIDER_KEY_ENV:raise ValueError("unknown provider")
 d=keys._read(); d["task_provider_"+task]=provider
 if model:d["task_model_"+task]=model
 else:d.pop("task_model_"+task,None)
 keys._write(d); return routes()[task]
def get_chain(task):
 import llm_providers as L
 r=routes().get(task) or routes()["bulk"]
 provider = "claude" if r.get("provider") == "anthropic" else r.get("provider")
 p=L.get_provider(provider)
 if p is None:
  return []
 if not p.available():
  return []
 if r.get("model") and hasattr(p,"model"):
  p.model=r["model"]
 # A task route is an explicit spend/model decision. Never silently fall back to another
 # vendor because that can consume a different API key and produce a materially different result.
 return [p]


def monthly_budget() -> int:
    try:
        return max(0, int(os.environ.get("ACE_LLM_MONTHLY_TOKEN_BUDGET", "1000000")))
    except ValueError:
        return 1000000

def _month_start() -> float:
    import datetime
    now=datetime.datetime.now(datetime.timezone.utc)
    return datetime.datetime(now.year,now.month,1,tzinfo=datetime.timezone.utc).timestamp()

def _active_reservations(db) -> int:
    from app.db import models as M
    from sqlalchemy import select, func
    cutoff = _month_start()
    return int(db.scalar(select(func.coalesce(func.sum(M.LLMBudgetReservation.estimated_tokens), 0)).where(
        M.LLMBudgetReservation.status == "active", M.LLMBudgetReservation.created_at >= cutoff)) or 0)


def usage_snapshot() -> dict:
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select, func
    with SessionLocal() as db:
        used=int(db.scalar(select(func.coalesce(func.sum(M.LLMUsage.estimated_tokens),0)).where(M.LLMUsage.created_at>=_month_start())) or 0)
        reserved=_active_reservations(db)
        rows=db.scalars(select(M.LLMUsage).where(M.LLMUsage.created_at>=_month_start()).order_by(M.LLMUsage.created_at.desc()).limit(50)).all()
    budget=monthly_budget()
    return {"budget":budget,"used":used,"reserved":reserved,"remaining":max(0,budget-used-reserved),"percent":round(((used+reserved)/budget*100),2) if budget else 0,
            "recent":[{"task":r.task,"provider":r.provider,"model":r.model,"tokens":r.estimated_tokens,"created_at":r.created_at} for r in rows]}


def _reserve(task: str, tokens: int):
    from app.db.base import SessionLocal
    from app.db import models as M
    from sqlalchemy import select, func
    with _BUDGET_LOCK:
        with SessionLocal() as db:
            budget=monthly_budget()
            used=int(db.scalar(select(func.coalesce(func.sum(M.LLMUsage.estimated_tokens),0)).where(M.LLMUsage.created_at>=_month_start())) or 0)
            reserved=_active_reservations(db)
            if budget and used+reserved+tokens>budget:
                raise RuntimeError(f"LLM monthly budget reached/reserved: {used+reserved:,}/{budget:,} estimated tokens. Increase ACE_LLM_MONTHLY_TOKEN_BUDGET or wait for the next month.")
            r=M.LLMBudgetReservation(task=task, estimated_tokens=int(tokens), status="active")
            db.add(r); db.commit(); db.refresh(r)
            return r.id


def _finish_reservation(reservation_id: int, task: str, res, prompt_tokens:int, output_tokens:int) -> None:
    from app.db.base import SessionLocal
    from app.db import models as M
    with _BUDGET_LOCK:
        with SessionLocal() as db:
            r=db.get(M.LLMBudgetReservation, reservation_id)
            if r and r.status == "active":
                r.status="consumed"
            db.add(M.LLMUsage(task=task,provider=res.provider,model=res.model,input_tokens=prompt_tokens,output_tokens=output_tokens,estimated_tokens=prompt_tokens+output_tokens))
            db.commit()


def _release_reservation(reservation_id: int) -> None:
    from app.db.base import SessionLocal
    from app.db import models as M
    with _BUDGET_LOCK:
        with SessionLocal() as db:
            r=db.get(M.LLMBudgetReservation,reservation_id)
            if r and r.status == "active":
                r.status="released"; db.commit()


class TaskLLM:
    """Task-aware MultiLLM facade with a real monthly token reservation. The reservation is
    persisted so concurrent jobs cannot oversubscribe the budget."""
    def __init__(self, task:str):
        import llm_providers as L
        self.task=task; self.providers=get_chain(task); self._multi=L.MultiLLM(self.providers)
    def available_providers(self): return self._multi.available_providers()
    def generate_list(self,prompt,*,n=None,max_tokens=8000):
        prompt_tokens=max(1,int(len(prompt.encode("utf-8"))/4))
        reserve_tokens=prompt_tokens+int(max_tokens or 0)
        rid=_reserve(self.task,reserve_tokens)
        try:
            res=self._multi.generate_list(prompt,n=n,max_tokens=max_tokens)
            out_tokens=max(1,int(sum(len(str(x).encode("utf-8"))/4 for x in res.expressions)))
            _finish_reservation(rid,self.task,res,prompt_tokens,out_tokens)
            return res
        except Exception:
            _release_reservation(rid)
            raise

