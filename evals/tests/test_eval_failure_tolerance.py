"""Tests for the ``--eval-failure-tolerance`` hook in evals/conftest.py.

We can't drive the real eval suite (it needs an LLM); instead spin up
sub-pytest sessions whose conftest just imports the hooks under test
and execute synthetic tests with deterministic outcomes.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# A self-contained conftest pulled directly from evals/conftest.py — only
# the bits relevant to failure tolerance, so the synthetic test session
# doesn't need DB fixtures, judge models, or any real eval infrastructure.
_TOLERANCE_HOOKS = textwrap.dedent('''
    import pytest

    _DEFAULT_EVAL_FAILURE_TOLERANCE = 3
    _TOLERANCE_CONFIG = None

    def pytest_addoption(parser):
        parser.addoption(
            "--eval-failure-tolerance",
            action="store",
            default=_DEFAULT_EVAL_FAILURE_TOLERANCE,
            type=int,
        )

    def pytest_configure(config):
        global _TOLERANCE_CONFIG
        _TOLERANCE_CONFIG = config
        config._failed_eval_nodeids = []
        config._failed_other_nodeids = []
        config.addinivalue_line("markers", "eval: eval-marked test")

    def pytest_runtest_logreport(report):
        if report.when != "call" or not report.failed:
            return
        config = _TOLERANCE_CONFIG
        if config is None:
            return
        if report.keywords.get("eval"):
            config._failed_eval_nodeids.append(report.nodeid)
        else:
            config._failed_other_nodeids.append(report.nodeid)

    @pytest.hookimpl(trylast=True)
    def pytest_sessionfinish(session, exitstatus):
        if exitstatus == 0:
            return
        config = session.config
        failed_eval = list(getattr(config, "_failed_eval_nodeids", []))
        failed_other = list(getattr(config, "_failed_other_nodeids", []))
        tolerance = config.getoption("--eval-failure-tolerance")
        if failed_other or not failed_eval or len(failed_eval) > tolerance:
            return
        print(
            f"\\n[evals] tolerating {len(failed_eval)} eval failure(s) "
            f"(<= {tolerance}); marking session as PASS:"
        )
        for nid in failed_eval:
            print(f"  - {nid}")
        session.exitstatus = 0
''')


def _write_session(tmp_path: Path, *, eval_failures: int, eval_passes: int = 0, non_eval_failures: int = 0) -> Path:
    """Materialize a tiny pytest project under tmp_path and return it."""
    (tmp_path / "conftest.py").write_text(_TOLERANCE_HOOKS)
    lines = ["import pytest", ""]
    for i in range(eval_failures):
        lines += [
            "@pytest.mark.eval",
            f"def test_eval_fail_{i}():",
            "    assert False",
            "",
        ]
    for i in range(eval_passes):
        lines += [
            "@pytest.mark.eval",
            f"def test_eval_pass_{i}():",
            "    assert True",
            "",
        ]
    for i in range(non_eval_failures):
        lines += [
            f"def test_non_eval_fail_{i}():",
            "    assert False",
            "",
        ]
    (tmp_path / "test_synth.py").write_text("\n".join(lines))
    return tmp_path


def _run_pytest(project: Path, *extra_args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *extra_args, str(project)],
        capture_output=True,
        text=True,
        cwd=project,
    )


def test_tolerates_three_eval_failures(tmp_path):
    project = _write_session(tmp_path, eval_failures=3)
    result = _run_pytest(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tolerating 3 eval failure(s)" in result.stdout


def test_fails_on_four_eval_failures(tmp_path):
    project = _write_session(tmp_path, eval_failures=4)
    result = _run_pytest(project)
    assert result.returncode != 0
    assert "tolerating" not in result.stdout


def test_does_not_tolerate_non_eval_failures(tmp_path):
    project = _write_session(tmp_path, eval_failures=1, non_eval_failures=1)
    result = _run_pytest(project)
    assert result.returncode != 0
    assert "tolerating" not in result.stdout


@pytest.mark.parametrize("budget,fails,expected_zero", [
    (1, 1, True),
    (1, 2, False),
    (0, 1, False),
])
def test_tolerance_flag_overrides_default(tmp_path, budget, fails, expected_zero):
    project = _write_session(tmp_path, eval_failures=fails)
    result = _run_pytest(project, f"--eval-failure-tolerance={budget}")
    if expected_zero:
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"tolerating {fails} eval failure(s)" in result.stdout
    else:
        assert result.returncode != 0


def test_passes_when_no_failures(tmp_path):
    project = _write_session(tmp_path, eval_failures=0, eval_passes=2)
    result = _run_pytest(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "tolerating" not in result.stdout
