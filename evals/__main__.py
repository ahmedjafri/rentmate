"""Dedicated local eval CLI.

Canonical usage:

    poetry run python -m evals run
    npm run evals -- --case test_maintenance
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.harness import DEFAULT_PASS_RATE, DEFAULT_TRIALS, safe_id, write_json

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _default_artifact_root(*, timestamp: str, case: str | None) -> Path:
    return _REPO_ROOT / "eval-runs" / f"{timestamp}-{safe_id(case or 'all')}"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _summarize(rows: list[dict[str, Any]], *, pass_rate: float) -> dict[str, Any]:
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("skipped"):
            continue
        by_case[row["case_id"]].append(row)

    cases = []
    for case_id, case_rows in sorted(by_case.items()):
        passed_trials = sum(1 for row in case_rows if row.get("passed"))
        trial_count = len(case_rows)
        rate = passed_trials / trial_count if trial_count else 0.0
        cases.append({
            "case_id": case_id,
            "trials": trial_count,
            "passed_trials": passed_trials,
            "pass_rate": rate,
            "passed": rate >= pass_rate,
            "artifact_dirs": [row.get("artifact_dir") for row in case_rows],
        })

    return {
        "total_cases": len(cases),
        "passed_cases": sum(1 for case in cases if case["passed"]),
        "pass_rate_threshold": pass_rate,
        "passed": all(case["passed"] for case in cases) and bool(cases),
        "cases": cases,
    }


def _print_summary(summary: dict[str, Any], artifact_root: Path) -> None:
    print("\n" + "=" * 72)
    print(f"Eval artifacts: {artifact_root}")
    print(f"Cases: {summary['passed_cases']}/{summary['total_cases']} passed")
    print(f"Pass-rate threshold: {summary['pass_rate_threshold']:.0%}")
    for case in summary["cases"]:
        status = "PASS" if case["passed"] else "FAIL"
        print(
            f"  {status:4s} {case['case_id']} "
            f"({case['passed_trials']}/{case['trials']} trials, {case['pass_rate']:.0%})"
        )


def run(args: argparse.Namespace) -> int:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    artifact_root = Path(args.out_dir) if args.out_dir else _default_artifact_root(timestamp=timestamp, case=args.case)
    artifact_root.mkdir(parents=True, exist_ok=True)

    base_pytest_args = ["-m", "eval", "--tb=short", "-s"]
    if args.case:
        base_pytest_args.extend(["-k", args.case])
    if args.pytest_args:
        extra = args.pytest_args[1:] if args.pytest_args[0] == "--" else args.pytest_args
        base_pytest_args.extend(extra)

    env_base = os.environ.copy()
    env_base.setdefault("RENTMATE_DISABLE_VECTOR_INDEX", "1")
    env_base.setdefault("RENTMATE_DISABLE_ASYNC_NOTIFICATIONS", "1")
    env_base["RENTMATE_EVAL_WRITE_ARTIFACTS"] = "1"
    env_base["RENTMATE_EVAL_ARTIFACT_ROOT"] = str(artifact_root)
    env_base["RENTMATE_EVAL_TRIALS"] = str(args.trials)

    subprocess_results = []
    for trial in range(1, args.trials + 1):
        print(f"\n--- Eval trial {trial}/{args.trials} ---", flush=True)
        env = env_base.copy()
        env["RENTMATE_EVAL_TRIAL_INDEX"] = str(trial)
        cmd = [sys.executable, "-m", "pytest", "evals", *base_pytest_args]
        result = subprocess.run(cmd, env=env)
        subprocess_results.append({"trial": trial, "returncode": result.returncode})
        if result.returncode != 0:
            print(f"Trial {trial} had pytest failures; aggregating by eval pass-rate.")

    rows = _load_jsonl(artifact_root / "eval-results.jsonl")
    summary = _summarize(rows, pass_rate=args.pass_rate)
    summary["trials_requested"] = args.trials
    summary["pytest_invocations"] = subprocess_results
    summary["artifact_root"] = str(artifact_root)
    write_json(artifact_root / "summary.json", summary)
    _print_summary(summary, artifact_root)

    if not rows:
        print("No eval result rows were written. Check collection errors or filters.")
        return 1
    return 0 if summary["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="Run evals with trials and local artifacts")
    run_parser.add_argument("--trials", type=int, default=int(os.getenv("EVAL_TRIALS", DEFAULT_TRIALS)))
    run_parser.add_argument("--pass-rate", type=float, default=float(os.getenv("EVAL_PASS_RATE", DEFAULT_PASS_RATE)))
    run_parser.add_argument("--case", help="pytest -k expression for a case, class, or file name")
    run_parser.add_argument("--out-dir", help="Artifact output directory")
    run_parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="Additional pytest args after --")
    run_parser.set_defaults(func=run)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
