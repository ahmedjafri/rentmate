"""
Wraps evals/runner.py as pytest tests.

Each eval case becomes a separate test so failures are reported individually.
Tests are skipped when LLM_API_KEY is not set (e.g. in CI without credentials).
Set LLM_API_KEY (and optionally LLM_MODEL / LLM_BASE_URL) to enable them.
"""
import os
import pytest
from evals.runner import load_cases, load_automation_meta, judge_case

_has_api_key = bool(os.getenv("LLM_API_KEY"))

def _all_cases():
    try:
        return load_cases()
    except Exception:
        return []

def pytest_generate_tests(metafunc):
    if "eval_case" in metafunc.fixturenames:
        cases = _all_cases()
        metafunc.parametrize("eval_case", cases, ids=[c.id for c in cases])


@pytest.mark.skipif(not _has_api_key, reason="LLM_API_KEY not set — skipping evals")
def test_eval(eval_case):
    meta = load_automation_meta(eval_case.automation)
    result = judge_case(eval_case, meta)

    if result.error:
        pytest.fail(f"Eval error: {result.error}")

    failed_criteria = [c for c in result.criteria_results if not c.passed]
    messages = []
    if not result.resolution_match:
        messages.append(f"Resolution mismatch (expected: {result.expected_resolution})")
    for c in failed_criteria:
        messages.append(f"FAIL [{c.criterion}]: {c.reason}")
    if result.notes:
        messages.append(f"Note: {result.notes}")

    assert result.passed, "\n".join(messages)
