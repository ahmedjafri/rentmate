"""Unit tests for the multi-actor harness internals.

These tests do NOT call the real LLM — they patch ``run_review`` to a
no-op so the harness's loop control (idle detection, turn counting) can
be exercised in default CI without an LLM key.
"""
import os
from datetime import UTC, datetime
from datetime import timedelta
from unittest.mock import patch

from evals.multi_actor_harness import Goal, _confirmed_appointment_times, run_e2e_scenario
from evals.harness import safe_id


def _noop_run_review(db, task):
    """Stand in for the agent: produce no messages, no suggestions."""
    return None


def test_harness_terminates_when_agent_goes_idle(db, scenario_builder):
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    sb.add_task(
        title="Quiet task",
        category="maintenance",
        urgency="low",
        context_body="Nothing to do.",
        goal="Do nothing — verify harness exits when no work happens.",
        steps=[],
    )
    s = sb.build()

    with patch("evals.multi_actor_harness.run_review", _noop_run_review):
        result = run_e2e_scenario(
            db,
            task=s["task"],
            actors=[],
            goal=Goal(
                description="Agent should resolve the task in one turn.",
            ),
            max_turns=10,
            judge_at_end=False,
        )

    assert result.terminated_by == "agent_idle"
    assert result.success is False
    # Two consecutive idle turns trigger the early exit.
    assert result.turns_taken == 2
    assert result.transcript == []


def test_harness_respects_max_turns_when_state_changes_each_turn(db, scenario_builder, monkeypatch):
    """If the DB state shifts every turn (so idle detection never trips),
    the harness should still cap at ``max_turns``."""
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    sb.add_task(
        title="Looping task",
        category="maintenance",
        urgency="low",
        context_body="Stub.",
        goal="Stub.",
        steps=[],
    )
    s = sb.build()
    task = s["task"]

    counter = {"n": 0}

    def fake_count_outbound(_db, _task):
        counter["n"] += 1
        return counter["n"]  # strictly increasing → never idle

    def fake_count_suggestions(_db, _task):
        return 0

    monkeypatch.setattr(
        "evals.multi_actor_harness._count_outbound_messages",
        fake_count_outbound,
    )
    monkeypatch.setattr(
        "evals.multi_actor_harness._count_suggestions",
        fake_count_suggestions,
    )

    with patch("evals.multi_actor_harness.run_review", _noop_run_review):
        result = run_e2e_scenario(
            db,
            task=task,
            actors=[],
            goal=Goal(description="never satisfied"),
            max_turns=3,
            judge_at_end=False,
        )

    assert result.terminated_by == "max_turns"
    assert result.turns_taken == 3
    assert result.success is False


def test_harness_predicate_short_circuits_loop(db, scenario_builder):
    """A passing predicate ends the run before the agent goes idle."""
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    sb.add_task(
        title="Predicate task",
        category="maintenance",
        urgency="low",
        context_body="Stub.",
        goal="Stub.",
        steps=[],
    )
    s = sb.build()

    with patch("evals.multi_actor_harness.run_review", _noop_run_review):
        result = run_e2e_scenario(
            db,
            task=s["task"],
            actors=[],
            goal=Goal(
                description="Always-true goal for testing.",
                predicate=lambda _db, _task: True,
            ),
            max_turns=10,
            judge_at_end=False,
        )

    assert result.terminated_by == "predicate"
    assert result.success is True
    assert result.turns_taken == 1


def test_harness_writes_turn_snapshots_when_artifacts_enabled(db, scenario_builder, monkeypatch, tmp_path):
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    sb.add_task(
        title="Snapshot task",
        category="maintenance",
        urgency="low",
        context_body="Stub.",
        goal="Stub.",
        steps=[],
    )
    s = sb.build()
    nodeid = "evals/test_e2e_harness_unit.py::test_snapshot_case"
    monkeypatch.setenv("RENTMATE_EVAL_WRITE_ARTIFACTS", "1")
    monkeypatch.setenv("RENTMATE_EVAL_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("RENTMATE_EVAL_TRIAL_INDEX", "2")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", f"{nodeid} (call)")

    with patch("evals.multi_actor_harness.run_review", _noop_run_review):
        result = run_e2e_scenario(
            db,
            task=s["task"],
            actors=[],
            goal=Goal(description="never satisfied"),
            max_turns=2,
            judge_at_end=False,
        )

    assert result.turns_taken == 2
    snapshot_dir = tmp_path / safe_id(nodeid) / "trial-002" / "state_snapshots"
    assert (snapshot_dir / "turn-001.json").exists()
    assert (snapshot_dir / "turn-002.json").exists()


def test_harness_advances_eval_clock_every_turn(db, scenario_builder, monkeypatch):
    sb = scenario_builder
    sb.add_property()
    sb.add_unit()
    sb.add_tenant()
    sb.add_lease()
    sb.add_task(
        title="Clock task",
        category="maintenance",
        urgency="low",
        context_body="Stub.",
        goal="Stub.",
        steps=[],
    )
    s = sb.build()
    seen = []
    monkeypatch.setenv("RENTMATE_EVAL_START_UTC", "2026-05-06T00:00:00+00:00")

    def capture_clock(_db, _task):
        import os

        seen.append(os.environ.get("RENTMATE_EVAL_NOW_UTC"))

    with patch("evals.multi_actor_harness.run_review", capture_clock):
        result = run_e2e_scenario(
            db,
            task=s["task"],
            actors=[],
            goal=Goal(description="never satisfied"),
            max_turns=3,
            judge_at_end=False,
            turn_time_advance=timedelta(hours=6),
        )

    assert result.turns_taken == 2
    assert seen == [
        "2026-05-06T00:00:00+00:00",
        "2026-05-06T06:00:00+00:00",
    ]
    assert "RENTMATE_EVAL_NOW_UTC" not in os.environ


def test_confirmed_appointment_times_ignore_proposed_windows():
    reference = datetime(2026, 5, 6, 10, 0, tzinfo=UTC)

    assert _confirmed_appointment_times(
        "I'll aim for a 2:00 PM window — does that work for the tenant?",
        reference=reference,
    ) == []


def test_confirmed_appointment_times_parse_confirmed_visit():
    reference = datetime(2026, 5, 6, 10, 0, tzinfo=UTC)

    assert _confirmed_appointment_times(
        "Got it, I'll be there at 2:00 PM today to check it out.",
        reference=reference,
    ) == [datetime(2026, 5, 6, 21, 0, tzinfo=UTC)]


def test_confirmed_appointment_times_parse_tomorrow_afternoon():
    reference = datetime(2026, 5, 6, 10, 0, tzinfo=UTC)

    assert _confirmed_appointment_times(
        "I'll plan to head over tomorrow afternoon to meet the tenant.",
        reference=reference,
    ) == [datetime(2026, 5, 8, 0, 0, tzinfo=UTC)]


def test_confirmed_appointment_times_parse_heading_over_now():
    reference = datetime(2026, 5, 6, 14, 10, tzinfo=UTC)

    assert _confirmed_appointment_times(
        "Perfect, I'll head over now and follow up once I have an estimate.",
        reference=reference,
    ) == [datetime(2026, 5, 6, 16, 10, tzinfo=UTC)]
