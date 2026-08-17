import json

from app.simulation.service import gate_thresholds
from app.core import llm_router


def test_gate_thresholds_are_defined_for_internal_calls():
    assert gate_thresholds(0) == {"sharpe": 2.69, "fitness": 1.5}
    assert gate_thresholds(1) == {"sharpe": 1.58, "fitness": 1.0}


def test_task_routes_are_canonical_and_known():
    r = llm_router.routes()
    assert set(r) == set(llm_router.DEFAULT_TASKS)
    assert r["research"]["provider"] in set(llm_router.DEFAULT_TASKS.values()) | {"claude"}


def test_no_remote_update_artifacts_in_build():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert not (root / "update_token.txt").exists()
    assert not (root / "update_url.txt").exists()
