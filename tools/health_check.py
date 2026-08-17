from pathlib import Path
import sys, compileall
ROOT=Path(__file__).resolve().parents[1]
print("ACE Studio local health check")
print("Python compilation:", compileall.compile_dir(str(ROOT/"backend/app"), quiet=1))
sys.path.insert(0,str(ROOT/"backend"))
from app.main import app
paths={getattr(r,"path","") for r in app.routes}
required=["/api/data/datasets","/api/data/fields","/api/knowledge/memory","/api/knowledge/memory/relevant","/api/evolution/simulate-variant","/api/settings/llm/routes"]
for p in required: print(p, "OK" if p in paths else "MISSING")
print("No LLM key is required for this health check.")
