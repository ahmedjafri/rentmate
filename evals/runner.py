#!/usr/bin/env python3
"""
Automation eval runner.

Loads dialog eval cases from evals/cases/*.json, replays each
conversation through an LLM judge, and scores whether the agent's behavior
meets the expected criteria for closing (or keeping open) a task.

Usage:
    python evals/runner.py                        # run all cases
    python evals/runner.py --case <id>            # single case
    python evals/runner.py --automation <key>     # filter by automation
    python evals/runner.py --out results.json     # save JSON report
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import indent
from typing import Any

# ── paths ──────────────────────────────────────────────────────────────────────

CASES_DIR = Path(__file__).parent / "cases"
AUTOMATIONS_DIR = Path(__file__).parent.parent / "automations"

# ── data model ────────────────────────────────────────────────────────────────


@dataclass
class DialogTurn:
    role: str        # "agent" | "user" | "system"
    content: str


@dataclass
class EvalCase:
    id: str
    automation: str
    description: str
    task: dict[str, Any]
    dialog: list[DialogTurn]
    expected: dict[str, Any]

    context: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EvalCase":
        return cls(
            id=data["id"],
            automation=data.get("automation"),
            description=data["description"],
            task=data.get("task"),
            context=data.get("context", ""),
            dialog=[DialogTurn(**t) for t in data["dialog"]],
            expected=data["expected"],
        )


@dataclass
class CriterionResult:
    criterion: str
    passed: bool
    reason: str


@dataclass
class EvalResult:
    case_id: str
    automation: str
    description: str
    passed: bool
    resolution_match: bool
    expected_resolution: str
    criteria_results: list[CriterionResult] = field(default_factory=list)
    notes: str = ""
    error: str = ""

    @property
    def score(self) -> float:
        if not self.criteria_results:
            return 0.0
        return sum(1 for c in self.criteria_results if c.passed) / len(self.criteria_results)


# ── loaders ───────────────────────────────────────────────────────────────────


def load_cases(
    case_id: str | None = None,
    automation: str | None = None,
) -> list[EvalCase]:
    cases = []
    for path in sorted(CASES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        if case_id and data["id"] != case_id:
            continue
        if automation and data["automation"] != automation:
            continue
        cases.append(EvalCase.from_dict(data))
    return cases


def load_automation_meta(key: str) -> dict[str, Any]:
    path = AUTOMATIONS_DIR / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


# ── judge ─────────────────────────────────────────────────────────────────────


def _format_dialog(turns: list[DialogTurn]) -> str:
    lines = []
    for turn in turns:
        prefix = {"agent": "AGENT", "user": "USER", "system": "SYSTEM"}.get(turn.role, turn.role.upper())
        lines.append(f"{prefix}: {turn.content}")
    return "\n\n".join(lines)


def judge_case(case: EvalCase, meta: dict[str, Any]) -> EvalResult:
    """Use an LLM to evaluate whether the dialog meets the expected criteria."""
    try:
        import litellm
    except ImportError:
        return EvalResult(
            case_id=case.id,
            automation=case.automation,
            description=case.description,
            passed=False,
            resolution_match=False,
            expected_resolution=case.expected.get("resolution", ""),
            error="litellm not installed — run: pip install litellm",
        )

    automation_label = meta.get("label", case.automation)
    automation_desc = meta.get("description", "")
    criteria = case.expected.get("criteria", [])
    expected_resolution = case.expected.get("resolution", "resolved")

    criteria_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))

    system_prompt = (
        "You are an expert evaluator for a property management AI assistant. "
        "Your job is to assess whether an agent's dialog performance meets specific quality criteria.\n\n"
        "Be objective and specific. A criterion passes only if the dialog clearly demonstrates it. "
        "If it is ambiguous or only partially met, mark it as failed."
    )

    task_title = case.task['title'] if case.task else '(no task — generic chat session)'
    task_body = case.task.get('body', '') if case.task else case.context
    automation_label = automation_label or 'general'

    user_prompt = f"""Evaluate the following task dialog.

AUTOMATION: {automation_label}
AUTOMATION DESCRIPTION: {automation_desc}

TASK TITLE: {task_title}
TASK BODY: {task_body}

DIALOG:
{_format_dialog(case.dialog)}

EXPECTED RESOLUTION: {expected_resolution}

CRITERIA TO EVALUATE:
{criteria_block}

For each criterion, determine whether the dialog passes or fails it.
Also determine whether the final resolution in the dialog matches the expected resolution ("{expected_resolution}").

Respond with ONLY a JSON object in exactly this format:
{{
  "resolution_match": true or false,
  "criteria": [
    {{"criterion": "exact text of criterion", "pass": true or false, "reason": "one sentence explanation"}},
    ...
  ],
  "overall": "pass" or "fail",
  "notes": "optional overall observation, max two sentences"
}}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    try:
        resp = litellm.completion(
            model=os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL") or None,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        verdict = json.loads(raw)
    except Exception as exc:
        return EvalResult(
            case_id=case.id,
            automation=case.automation,
            description=case.description,
            passed=False,
            resolution_match=False,
            expected_resolution=expected_resolution,
            error=str(exc),
        )

    criteria_results = [
        CriterionResult(
            criterion=c.get("criterion", ""),
            passed=bool(c.get("pass", False)),
            reason=c.get("reason", ""),
        )
        for c in verdict.get("criteria", [])
    ]

    passed = verdict.get("overall", "fail") == "pass"
    resolution_match = bool(verdict.get("resolution_match", False))

    return EvalResult(
        case_id=case.id,
        automation=case.automation,
        description=case.description,
        passed=passed,
        resolution_match=resolution_match,
        expected_resolution=expected_resolution,
        criteria_results=criteria_results,
        notes=verdict.get("notes", ""),
    )


# ── reporting ─────────────────────────────────────────────────────────────────

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_RESET = "\033[0m"
_USE_COLOR = sys.stdout.isatty()


def _c(color: str, text: str) -> str:
    return f"{color}{text}{_RESET}" if _USE_COLOR else text


def print_result(result: EvalResult) -> None:
    status = _c(_GREEN, "PASS") if result.passed else _c(_RED, "FAIL")
    print(f"\n{'─'*60}")
    print(f"{_c(_BOLD, result.case_id)}  [{status}]  score: {result.score:.0%}")
    print(f"  {result.description}")

    if result.error:
        print(f"  {_c(_RED, 'ERROR:')} {result.error}")
        return

    res_ok = result.resolution_match
    res_color = _GREEN if res_ok else _RED
    print(f"  Resolution: {_c(res_color, result.expected_resolution)} {'✓' if res_ok else '✗'}")

    for cr in result.criteria_results:
        mark = _c(_GREEN, "✓") if cr.passed else _c(_RED, "✗")
        print(f"  {mark} {cr.criterion}")
        if not cr.passed:
            print(f"    {_c(_YELLOW, cr.reason)}")

    if result.notes:
        print(f"  {_c(_YELLOW, 'Note:')} {result.notes}")


def print_summary(results: list[EvalResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    errored = sum(1 for r in results if r.error)
    avg_score = sum(r.score for r in results) / total if total else 0.0

    print(f"\n{'═'*60}")
    print(_c(_BOLD, f"RESULTS: {passed}/{total} passed  |  avg score {avg_score:.0%}"))
    if errored:
        print(_c(_RED, f"  {errored} case(s) errored — check LLM_API_KEY / LLM_MODEL"))

    by_automation: dict[str, list[EvalResult]] = {}
    for r in results:
        by_automation.setdefault(r.automation, []).append(r)

    for auto, auto_results in sorted(by_automation.items()):
        n_pass = sum(1 for r in auto_results if r.passed)
        color = _GREEN if n_pass == len(auto_results) else (_YELLOW if n_pass > 0 else _RED)
        print(f"  {_c(color, auto):40s}  {n_pass}/{len(auto_results)}")


def results_to_dict(results: list[EvalResult]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.case_id,
            "automation": r.automation,
            "passed": r.passed,
            "score": round(r.score, 3),
            "resolution_match": r.resolution_match,
            "expected_resolution": r.expected_resolution,
            "criteria": [
                {"criterion": c.criterion, "pass": c.passed, "reason": c.reason}
                for c in r.criteria_results
            ],
            "notes": r.notes,
            "error": r.error,
        }
        for r in results
    ]


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Run automation dialog evals")
    parser.add_argument("--case", metavar="ID", help="Run a single case by ID")
    parser.add_argument("--automation", metavar="KEY", help="Filter by automation key")
    parser.add_argument("--out", metavar="FILE", help="Write JSON results to file")
    parser.add_argument("--list", action="store_true", help="List available cases and exit")
    args = parser.parse_args()

    cases = load_cases(case_id=args.case, automation=args.automation)

    if not cases:
        print("No matching cases found.")
        sys.exit(1)

    if args.list:
        for c in cases:
            print(f"{c.id:50s}  {c.automation}")
        return

    print(f"Running {len(cases)} eval case(s)…")

    results: list[EvalResult] = []
    for case in cases:
        meta = load_automation_meta(case.automation)
        sys.stdout.write(f"  {case.id} … ")
        sys.stdout.flush()
        result = judge_case(case, meta)
        status = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
        sys.stdout.write(f"{status}\n")
        results.append(result)

    for result in results:
        print_result(result)

    print_summary(results)

    if args.out:
        Path(args.out).write_text(json.dumps(results_to_dict(results), indent=2))
        print(f"\nResults written to {args.out}")

    sys.exit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
