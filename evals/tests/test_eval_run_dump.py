"""Unit test for the eval-failure run-dump helper in evals/conftest.py."""
import importlib.util
import json
import sys
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from db.models import AgentRun, AgentTrace


@pytest.fixture(scope="module")
def _eval_conftest():
    spec = importlib.util.spec_from_file_location(
        "_eval_conftest_for_dump_test",
        Path(__file__).resolve().parents[1] / "conftest.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_dump_eval_runs_writes_file_with_runs_and_traces(db, _eval_conftest, tmp_path, monkeypatch):
    monkeypatch.setattr(_eval_conftest, "_EVAL_RUN_DUMP_DIR", tmp_path)

    run = AgentRun(
        id=str(uuid.uuid4()),
        org_id=1,
        creator_id=1,
        source="assess",
        status="completed",
        agent_version="test",
        execution_path="local",
        trigger_input="hello",
        final_response="hi",
        started_at=datetime.now(UTC),
        ended_at=datetime.now(UTC),
        run_metadata={"flag": True},
    )
    db.add(run)
    db.flush()
    db.add(AgentTrace(
        org_id=1, creator_id=1, run_id=run.id, sequence_num=0,
        trace_type="tool_call", source="chat", tool_name="lookup_properties",
        summary="Looking up properties",
        detail=json.dumps({"query": "bothell"}),
        timestamp=datetime.now(UTC),
    ))
    db.flush()

    fake_item = types.SimpleNamespace(
        funcargs={"db": db},
        name="my_eval_test",
        nodeid="evals/test_smoke.py::my_eval_test",
    )
    fake_report = types.SimpleNamespace()
    path = _eval_conftest._dump_eval_runs(fake_item, fake_report)

    assert path is not None
    p = Path(path)
    assert p.exists() and p.parent == tmp_path
    assert p.name.startswith("my_eval_test-")
    blob = json.loads(p.read_text())
    assert blob["test"] == "evals/test_smoke.py::my_eval_test"
    assert len(blob["runs"]) == 1
    dumped_run = blob["runs"][0]
    assert dumped_run["trigger_input"] == "hello"
    assert dumped_run["final_response"] == "hi"
    assert dumped_run["metadata"] == {"flag": True}
    assert len(dumped_run["traces"]) == 1
    assert dumped_run["traces"][0]["tool_name"] == "lookup_properties"
    assert dumped_run["traces"][0]["detail"] == json.dumps({"query": "bothell"})


def test_dump_eval_runs_returns_none_when_no_runs(db, _eval_conftest, tmp_path, monkeypatch):
    monkeypatch.setattr(_eval_conftest, "_EVAL_RUN_DUMP_DIR", tmp_path)
    fake_item = types.SimpleNamespace(funcargs={"db": db}, name="empty", nodeid="evals/test_empty.py::empty")
    assert _eval_conftest._dump_eval_runs(fake_item, types.SimpleNamespace()) is None
    assert list(tmp_path.iterdir()) == []


def test_dump_eval_runs_returns_none_when_db_fixture_missing(_eval_conftest, tmp_path, monkeypatch):
    monkeypatch.setattr(_eval_conftest, "_EVAL_RUN_DUMP_DIR", tmp_path)
    fake_item = types.SimpleNamespace(funcargs={}, name="x", nodeid="evals/x.py::x")
    assert _eval_conftest._dump_eval_runs(fake_item, types.SimpleNamespace()) is None
