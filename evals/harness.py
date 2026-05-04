"""Local-first eval harness primitives.

The CLI in ``evals.__main__`` uses pytest as the process runner, but eval
semantics live here: trial aggregation, scorer results, and artifacts.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

DEFAULT_TRIALS = 3
DEFAULT_SCORE_THRESHOLD = 0.80
DEFAULT_PASS_RATE = 2 / 3


def safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "eval"


@dataclass
class ScoreResult:
    name: str
    score: float
    passed: bool
    weight: float = 1.0
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "score": round(float(self.score), 4),
            "passed": bool(self.passed),
            "weight": float(self.weight),
            "reason": self.reason,
            "detail": self.detail,
        }


def weighted_score(scores: list[ScoreResult]) -> float:
    weights = [max(0.0, float(score.weight)) for score in scores]
    denom = sum(weights)
    if denom <= 0:
        return 0.0
    return sum(float(score.score) * weight for score, weight in zip(scores, weights)) / denom


class ToolCallScorer:
    """Score ATIF trajectories by expected tool-call names."""

    def __init__(self, *, expected: list[str], weight: float = 1.0, name: str = "tool_calls"):
        self.expected = expected
        self.weight = weight
        self.name = name

    def __call__(self, *, trajectory: dict[str, Any] | None, **_kwargs) -> ScoreResult:
        found: list[str] = []
        for step in (trajectory or {}).get("steps", []):
            for call in step.get("tool_calls") or []:
                tool_name = call.get("function_name")
                if tool_name:
                    found.append(tool_name)
        missing = [tool for tool in self.expected if tool not in found]
        passed = not missing
        score = 1.0 if passed else (len(self.expected) - len(missing)) / max(1, len(self.expected))
        return ScoreResult(
            name=self.name,
            score=score,
            passed=passed,
            weight=self.weight,
            reason="All expected tools were called." if passed else f"Missing tool calls: {missing}",
            detail={"expected": self.expected, "found": found, "missing": missing},
        )


class PythonScorer:
    """Wrap a custom scorer callable that returns bool, number, or ScoreResult."""

    def __init__(self, name: str, fn: Callable[..., bool | float | ScoreResult], *, weight: float = 1.0):
        self.name = name
        self.fn = fn
        self.weight = weight

    def __call__(self, **kwargs) -> ScoreResult:
        result = self.fn(**kwargs)
        if isinstance(result, ScoreResult):
            return result
        if isinstance(result, bool):
            return ScoreResult(name=self.name, score=1.0 if result else 0.0, passed=result, weight=self.weight)
        score = float(result)
        return ScoreResult(name=self.name, score=score, passed=score >= DEFAULT_SCORE_THRESHOLD, weight=self.weight)


class JudgeScorer:
    """LLM-as-judge scorer with weighted criteria."""

    def __init__(self, *, scenario: str, criteria: list[str], weight: float = 1.0, name: str = "judge"):
        self.scenario = scenario
        self.criteria = criteria
        self.weight = weight
        self.name = name

    def __call__(self, *, output: str, **_kwargs) -> ScoreResult:
        from evals.conftest import judge_message

        verdict = judge_message(output, self.scenario, self.criteria)
        scores = verdict.get("scores") or {}
        numeric_scores = [float(value) / 5.0 for value in scores.values()]
        score = sum(numeric_scores) / len(numeric_scores) if numeric_scores else (1.0 if verdict.get("pass") else 0.0)
        passed = bool(verdict.get("pass")) and score >= DEFAULT_SCORE_THRESHOLD
        return ScoreResult(
            name=self.name,
            score=score,
            passed=passed,
            weight=self.weight,
            reason=str(verdict.get("reason") or ""),
            detail={"criteria": self.criteria, "raw": verdict},
        )


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False))


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, default=str, ensure_ascii=False) + "\n")


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()

