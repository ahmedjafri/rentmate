from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evals.__main__ import _default_artifact_root, _summarize, run as run_eval_cli
from evals.harness import ScoreResult, ToolCallScorer, weighted_score


def test_weighted_score_combines_scorers():
    scores = [
        ScoreResult(name="tool", score=1.0, passed=True, weight=2.0),
        ScoreResult(name="judge", score=0.5, passed=False, weight=1.0),
    ]

    assert weighted_score(scores) == 2.5 / 3.0


def test_tool_call_scorer_reads_atif_trajectory():
    scorer = ToolCallScorer(expected=["lookup_vendors", "message_person"])
    result = scorer(
        trajectory={
            "steps": [
                {
                    "tool_calls": [
                        {"function_name": "lookup_vendors"},
                        {"function_name": "message_person"},
                    ],
                },
            ],
        },
    )

    assert result.passed is True
    assert result.score == 1.0


def test_tool_call_scorer_reports_missing_tools():
    scorer = ToolCallScorer(expected=["lookup_vendors", "message_person"])
    result = scorer(trajectory={"steps": [{"tool_calls": [{"function_name": "lookup_vendors"}]}]})

    assert result.passed is False
    assert result.score == 0.5
    assert result.detail["missing"] == ["message_person"]


def test_summary_uses_pass_rate_threshold():
    summary = _summarize(
        [
            {"case_id": "eval_a", "passed": True},
            {"case_id": "eval_a", "passed": True},
            {"case_id": "eval_a", "passed": False},
            {"case_id": "eval_b", "passed": True},
            {"case_id": "eval_b", "passed": False},
            {"case_id": "eval_b", "passed": False},
            {"case_id": "eval_skipped", "skipped": True, "passed": False},
        ],
        pass_rate=2 / 3,
    )

    assert summary["passed"] is False
    assert summary["passed_cases"] == 1
    assert summary["total_cases"] == 2
    assert summary["cases"][0]["passed"] is True
    assert summary["cases"][1]["passed"] is False


def test_default_artifact_root_is_repo_local():
    root = _default_artifact_root(timestamp="20260503-192831", case=None)

    assert root == Path(__file__).resolve().parents[2] / "eval-runs" / "20260503-192831-all"


def test_eval_cli_disables_pytest_capture(tmp_path, monkeypatch):
    commands = []

    def fake_run(cmd, env):
        commands.append(cmd)
        root = Path(env["RENTMATE_EVAL_ARTIFACT_ROOT"])
        (root / "eval-results.jsonl").write_text(json.dumps({"case_id": "eval_a", "passed": True}) + "\n")
        return argparse.Namespace(returncode=0)

    monkeypatch.setattr("evals.__main__.subprocess.run", fake_run)

    result = run_eval_cli(argparse.Namespace(
        trials=1,
        pass_rate=1.0,
        case=None,
        out_dir=str(tmp_path),
        pytest_args=[],
    ))

    assert result == 0
    assert commands == [[sys.executable, "-m", "pytest", "evals", "-m", "eval", "--tb=short", "-s"]]
