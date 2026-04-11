#!/usr/bin/env python3
"""Micro-benchmark the suggestion action path on an isolated SQLite DB."""

from __future__ import annotations

import argparse
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from time import perf_counter

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backends.local_auth import reset_request_context, set_request_context
from db.enums import AgentSource, SuggestionOption, TaskCategory, Urgency
from db.models import Base, User
from gql.services import suggestion_service
from gql.services.task_suggestions import CreateTaskSuggestionExecutor


def _seed_db(session_factory) -> str:
    session = session_factory()
    try:
        owner = User(
            id=1,
            org_id=1,
            creator_id=1,
            email="owner@example.com",
            first_name="Owner",
            last_name="User",
            user_type="account",
            active=True,
        )
        vendor = User(
            org_id=1,
            creator_id=1,
            first_name="Vince",
            last_name="Vendor",
            user_type="vendor",
            active=True,
        )
        session.add_all([owner, vendor])
        session.commit()
        return str(vendor.external_id)
    finally:
        session.close()


def _create_pending_suggestion(session_factory, *, with_action_payload: bool, vendor_external_id: str) -> int:
    session = session_factory()
    token = set_request_context(account_id=1, org_id=1)
    try:
        suggestion = suggestion_service.create_suggestion(
            session,
            title="Review lease renewal",
            ai_context="Lease for Bob Ferguson expires soon.",
            category=TaskCategory.LEASING,
            urgency=Urgency.MEDIUM,
            source=AgentSource(),
            options=[
                SuggestionOption(key="accept", label="Create task", action="send_and_create_task", variant="default"),
                SuggestionOption(key="dismiss", label="Dismiss", action="reject_task", variant="secondary"),
            ],
            action_payload={
                "action": "send_and_create_task",
                "vendor_id": vendor_external_id,
                "vendor_name": "Vince Vendor",
                "draft_message": "Hi Bob, your lease is coming up for renewal.",
            } if with_action_payload else {
                "action": "send_and_create_task",
                "vendor_id": vendor_external_id,
                "vendor_name": "Vince Vendor",
            },
        )
        session.commit()
        return suggestion.id
    finally:
        reset_request_context(token)
        session.close()


def _benchmark_dismiss(session_factory, iterations: int, vendor_external_id: str) -> list[float]:
    samples_ms: list[float] = []
    for _ in range(iterations):
        suggestion_id = _create_pending_suggestion(
            session_factory,
            with_action_payload=False,
            vendor_external_id=vendor_external_id,
        )
        session = session_factory()
        token = set_request_context(account_id=1, org_id=1)
        try:
            started = perf_counter()
            suggestion_service.act_on_suggestion(session, suggestion_id, "reject_task")
            session.commit()
            samples_ms.append((perf_counter() - started) * 1000)
        finally:
            reset_request_context(token)
            session.close()
    return samples_ms


def _benchmark_accept(session_factory, iterations: int, vendor_external_id: str) -> tuple[list[float], dict[str, list[float]]]:
    totals_ms: list[float] = []
    breakdown_ms: dict[str, list[float]] = defaultdict(list)

    for _ in range(iterations):
        suggestion_id = _create_pending_suggestion(
            session_factory,
            with_action_payload=True,
            vendor_external_id=vendor_external_id,
        )
        session = session_factory()
        token = set_request_context(account_id=1, org_id=1)
        try:
            executor = CreateTaskSuggestionExecutor.for_suggestion(session, suggestion_id)

            original_fetch = executor._fetch_suggestion
            original_create_task = executor._create_task_from_suggestion
            original_resolve = executor._resolve_suggestion
            original_send = executor._send_draft_message

            def timed(name, fn):
                def wrapper(*args, **kwargs):
                    started = perf_counter()
                    try:
                        return fn(*args, **kwargs)
                    finally:
                        breakdown_ms[name].append((perf_counter() - started) * 1000)

                return wrapper

            executor._fetch_suggestion = timed("fetch_suggestion", original_fetch)
            executor._create_task_from_suggestion = timed("create_task_from_suggestion", original_create_task)
            executor._resolve_suggestion = timed("resolve_suggestion", original_resolve)
            executor._send_draft_message = timed("send_draft_message", original_send)

            started = perf_counter()
            executor.execute(suggestion_id, "send_and_create_task")
            session.commit()
            totals_ms.append((perf_counter() - started) * 1000)
        finally:
            reset_request_context(token)
            session.close()

    return totals_ms, breakdown_ms


def _print_stats(label: str, samples_ms: list[float]) -> None:
    print(
        f"{label}: n={len(samples_ms)} "
        f"mean={statistics.mean(samples_ms):.2f}ms "
        f"median={statistics.median(samples_ms):.2f}ms "
        f"p95={sorted(samples_ms)[max(0, int(len(samples_ms) * 0.95) - 1)]:.2f}ms "
        f"min={min(samples_ms):.2f}ms "
        f"max={max(samples_ms):.2f}ms"
    )


def run_benchmarks(*, iterations: int = 50) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="rentmate-suggestion-bench-") as tmpdir:
        db_path = Path(tmpdir) / "bench.db"
        engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
        Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(engine)
        vendor_external_id = _seed_db(Session)

        dismiss_ms = _benchmark_dismiss(Session, iterations, vendor_external_id)
        accept_ms, breakdown_ms = _benchmark_accept(Session, iterations, vendor_external_id)

    return {
        "db_path": str(db_path),
        "dismiss_ms": dismiss_ms,
        "accept_ms": accept_ms,
        "breakdown_ms": breakdown_ms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=50)
    args = parser.parse_args()

    result = run_benchmarks(iterations=args.iterations)
    print(f"isolated_db={result['db_path']}")
    dismiss_ms = result["dismiss_ms"]
    accept_ms = result["accept_ms"]
    breakdown_ms = result["breakdown_ms"]
    _print_stats("dismiss", dismiss_ms)
    _print_stats("accept", accept_ms)
    for name in sorted(breakdown_ms):
        _print_stats(f"accept.{name}", breakdown_ms[name])


if __name__ == "__main__":
    main()
