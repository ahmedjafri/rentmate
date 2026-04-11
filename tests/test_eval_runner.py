from __future__ import annotations

from evals.runner import EvalResult, print_summary


def test_print_summary_handles_missing_automation(capsys) -> None:
    print_summary(
        [
            EvalResult(
                case_id="case-1",
                automation=None,
                description="missing automation",
                passed=False,
                resolution_match=False,
                expected_resolution="active",
                error="missing key",
            )
        ]
    )
    out = capsys.readouterr().out
    assert "unassigned" in out
    assert "1 case(s) errored" in out
