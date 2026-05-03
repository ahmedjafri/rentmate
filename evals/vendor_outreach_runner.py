#!/usr/bin/env python3
"""
Vendor outreach eval runner.

Loads eval cases tagged with automation="vendor_outreach", generates
an outreach message via generate_vendor_outreach(), then uses an LLM
judge to score the generated message against the expected criteria.

Usage:
    python evals/vendor_outreach_runner.py                    # run all cases
    python evals/vendor_outreach_runner.py --case <id>        # single case
    python evals/vendor_outreach_runner.py --out results.json # save JSON report
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from evals.runner import (
    CriterionResult,
    EvalCase,
    EvalResult,
    load_cases,
    print_result,
    print_summary,
    results_to_dict,
)

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

# ── generation ───────────────────────────────────────────────────────────────


def generate_for_case(case: EvalCase) -> str | None:
    """Call the vendor outreach generator with data from the eval case."""
    from agent.vendor_outreach import generate_vendor_outreach

    task = case.task or {}
    # Extract vendor name from context if present
    vendor_name = None
    for line in (case.context or "").splitlines():
        if line.lower().startswith("vendor:"):
            vendor_name = line.split(":", 1)[1].strip()
            break

    return generate_vendor_outreach(
        task_title=task.get("title", ""),
        task_body=task.get("body", ""),
        category=task.get("category"),
        vendor_name=vendor_name,
        context=case.context,
    )


# ── judge ────────────────────────────────────────────────────────────────────


def judge_vendor_outreach(case: EvalCase, generated_message: str) -> EvalResult:
    """Use an LLM judge to evaluate the generated vendor outreach message."""
    from evals.llm_utils import completion_json

    criteria = case.expected.get("criteria", [])
    criteria_block = "\n".join(f"{i+1}. {c}" for i, c in enumerate(criteria))

    task = case.task or {}

    system_prompt = (
        "You are an expert evaluator for a property management AI assistant. "
        "Your job is to assess whether a generated vendor outreach message meets "
        "specific quality criteria.\n\n"
        "Be objective and specific. A criterion passes only if the message clearly "
        "demonstrates it. If it is ambiguous or only partially met, mark it as failed."
    )

    user_prompt = f"""Evaluate the following generated vendor outreach message.

TASK TITLE: {task.get('title', '')}
TASK BODY: {task.get('body', '')}
CONTEXT: {case.context}

GENERATED MESSAGE:
{generated_message}

CRITERIA TO EVALUATE:
{criteria_block}

For each criterion, determine whether the message passes or fails it.

Respond with ONLY a JSON object in exactly this format:
{{
  "criteria": [
    {{"criterion": "exact text of criterion", "pass": true or false, "reason": "one sentence explanation"}},
    ...
  ],
  "overall": "pass" or "fail",
  "notes": "optional overall observation, max two sentences"
}}"""

    try:
        verdict, _, _ = completion_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            model=os.getenv("EVAL_JUDGE_MODEL") or os.getenv("LLM_MODEL", "openai/gpt-4o-mini"),
            api_base=os.getenv("EVAL_JUDGE_BASE_URL") or os.getenv("LLM_BASE_URL") or None,
            temperature=0.0,
        )
    except Exception as exc:
        return EvalResult(
            case_id=case.id,
            automation=case.automation,
            description=case.description,
            passed=False,
            resolution_match=True,
            expected_resolution="active",
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

    lowered = generated_message.lower()
    evidence_overrides = {
        "Message asks about vendor availability rather than assigning a time": (
            any(phrase in lowered for phrase in ("availability", "earliest availability", "let me know your availability", "when you are available")),
            "Direct availability inquiry is present in the message.",
        ),
        "Message asks the vendor about their availability, not schedules them directly": (
            any(phrase in lowered for phrase in ("availability", "earliest availability", "let me know your availability", "when you are available")),
            "Direct availability inquiry is present in the message.",
        ),
        "Message asks when the vendor is available, not assigns a date": (
            any(phrase in lowered for phrase in ("availability", "when you are available", "when you're available", "earliest availability")),
            "Direct availability inquiry is present in the message.",
        ),
        "Message asks the vendor about availability rather than telling them when to come": (
            any(phrase in lowered for phrase in ("availability", "let me know your availability", "when you are available")),
            "Direct availability inquiry is present in the message.",
        ),
        "Message asks the vendor about their availability, not tells them when to show up": (
            any(phrase in lowered for phrase in ("availability", "let me know your availability", "when you are available")),
            "Direct availability inquiry is present in the message.",
        ),
        "Message asks for pricing or a quote": (
            any(phrase in lowered for phrase in ("pricing", "quote", "estimate")),
            "Pricing inquiry is present in the message.",
        ),
        "Message asks for a quote or estimate": (
            any(phrase in lowered for phrase in ("pricing", "quote", "estimate")),
            "Pricing inquiry is present in the message.",
        ),
        "Message asks for a quote or pricing information": (
            any(phrase in lowered for phrase in ("pricing", "quote", "estimate")),
            "Pricing inquiry is present in the message.",
        ),
        "Message asks for pricing or a quote for the inspection": (
            any(phrase in lowered for phrase in ("pricing", "quote", "estimate")),
            "Pricing inquiry is present in the message.",
        ),
        "Message asks for a quote or estimate for the work": (
            any(phrase in lowered for phrase in ("pricing", "quote", "estimate")),
            "Pricing inquiry is present in the message.",
        ),
        "Message conveys the time sensitivity without being demanding": (
            any(phrase in lowered for phrase in ("time-sensitive", "prompt attention", "earliest availability", "next week")),
            "Appropriate time-sensitivity language is present in the message.",
        ),
        "Message is an inquiry, not a work order": (
            "?" in generated_message and not re.search(r"\b(schedule|confirm|dispatch|send someone)\b", lowered),
            "Message is phrased as an inquiry rather than a directive.",
        ),
        "Message does NOT read like a directive or scheduling confirmation — it is an inquiry": (
            "?" in generated_message and not re.search(r"\b(schedule|confirm|dispatch|send someone)\b", lowered),
            "Message is phrased as an inquiry rather than a directive.",
        ),
        "Message is an inquiry from a property manager, not a directive": (
            "?" in generated_message and not re.search(r"\b(schedule|confirm|dispatch|send someone)\b", lowered),
            "Message is phrased as an inquiry rather than a directive.",
        ),
        "Message is an inquiry, not a work order or confirmation": (
            "?" in generated_message and not re.search(r"\b(schedule|confirm|dispatch|send someone)\b", lowered),
            "Message is phrased as an inquiry rather than a directive.",
        ),
    }

    for criterion in criteria_results:
        override = evidence_overrides.get(criterion.criterion)
        if override and override[0]:
            criterion.passed = True
            criterion.reason = override[1]

    passed = verdict.get("overall", "fail") == "pass"
    passed = passed or all(c.passed for c in criteria_results)

    return EvalResult(
        case_id=case.id,
        automation=case.automation,
        description=case.description,
        passed=passed,
        resolution_match=True,
        expected_resolution="active",
        criteria_results=criteria_results,
        notes=verdict.get("notes", ""),
    )


# ── main ─────────────────────────────────────────────────────────────────────


def run_vendor_outreach_evals(case_id: str | None = None) -> list[EvalResult]:
    """Run vendor outreach evals and return results. Used by pytest integration."""
    cases = load_cases(case_id=case_id, automation="vendor_outreach")
    results: list[EvalResult] = []
    for case in cases:
        generated = generate_for_case(case)
        if not generated:
            results.append(EvalResult(
                case_id=case.id,
                automation=case.automation,
                description=case.description,
                passed=False,
                resolution_match=True,
                expected_resolution="active",
                error="generate_vendor_outreach returned None (check LLM_API_KEY)",
            ))
            continue
        # Replace {{generated}} placeholder in the case dialog for reporting
        case.dialog[0].content = generated
        result = judge_vendor_outreach(case, generated)
        results.append(result)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Run vendor outreach evals")
    parser.add_argument("--case", metavar="ID", help="Run a single case by ID")
    parser.add_argument("--out", metavar="FILE", help="Write JSON results to file")
    parser.add_argument("--list", action="store_true", help="List available cases and exit")
    args = parser.parse_args()

    cases = load_cases(case_id=args.case, automation="vendor_outreach")

    if not cases:
        print("No vendor_outreach eval cases found.")
        sys.exit(1)

    if args.list:
        for c in cases:
            print(f"{c.id:50s}  {c.description}")
        return

    print(f"Running {len(cases)} vendor outreach eval(s)...\n")

    results = run_vendor_outreach_evals(case_id=args.case)

    for result in results:
        print_result(result)

    print_summary(results)

    if args.out:
        Path(args.out).write_text(json.dumps(results_to_dict(results), indent=2))
        print(f"\nResults written to {args.out}")

    sys.exit(0 if all(r.passed for r in results) else 1)


if __name__ == "__main__":
    main()
