"""Restore an eval turn snapshot and boot RentMate against it."""
from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from evals.state_snapshots import load_state_snapshot, restore_state_snapshot

_REPO_ROOT = Path(__file__).resolve().parents[1]

_EPHEMERAL_PG_IMAGE = "postgres:16-alpine"
_EPHEMERAL_PG_USER = "postgres"
_EPHEMERAL_PG_PASSWORD = "postgres"
_EPHEMERAL_PG_DB = "rentmate"
_EPHEMERAL_READY_TIMEOUT_SECONDS = 30.0


def replay(args: argparse.Namespace) -> int:
    snapshot_path = _resolve_snapshot_path(args.run, args.trial, args.turn)
    snapshot = load_state_snapshot(snapshot_path)

    db_uri_cm: contextlib.AbstractContextManager[str]
    if args.db_uri:
        db_uri_cm = contextlib.nullcontext(args.db_uri)
    else:
        db_uri_cm = _ephemeral_postgres_container()

    with db_uri_cm as db_uri:
        _ensure_database_exists(db_uri)

        engine = create_engine(db_uri)
        restore_state_snapshot(engine, snapshot)
        engine.dispose()

        print(f"Restored {snapshot_path}")
        print(f"Replay database: {db_uri}")

        if args.no_server:
            return 0

        env = os.environ.copy()
        env["RENTMATE_DB_URI"] = db_uri
        env["DATABASE_URL"] = db_uri
        env["RENTMATE_ENV"] = args.env
        env["RENTMATE_STARTUP_TASKS"] = "skip"
        env["RENTMATE_DISABLE_VECTOR_INDEX"] = "1"
        env["RENTMATE_DISABLE_ASYNC_NOTIFICATIONS"] = "1"
        env["RENTMATE_DEMO_SIMULATOR"] = "0"

        url = f"http://{args.host}:{args.port}"
        print(f"Starting RentMate replay at {url}")
        print("Background DB-dependent startup tasks are disabled with RENTMATE_STARTUP_TASKS=skip.")

        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--log-level",
            args.log_level,
        ]
        return subprocess.run(command, cwd=_REPO_ROOT, env=env).returncode


def _resolve_snapshot_path(run: str, trial: int, turn: int) -> Path:
    run_path = Path(run).expanduser()
    if not run_path.is_absolute():
        candidate = _REPO_ROOT / "eval-runs" / run_path
        run_path = candidate if candidate.exists() else (_REPO_ROOT / run_path)

    suffix = Path(f"trial-{trial:03d}") / "state_snapshots" / f"turn-{turn:03d}.json"
    direct = run_path / suffix
    if direct.exists():
        return direct

    # The run folder usually contains a per-case subdirectory (named after
    # ``safe_id(nodeid)``), so the snapshot lives one level deeper. Auto-
    # discover it when the direct path misses; require an unambiguous match.
    nested_matches = sorted(run_path.glob(f"*/{suffix}"))
    if len(nested_matches) == 1:
        return nested_matches[0]
    if len(nested_matches) > 1:
        cases = ", ".join(sorted({p.relative_to(run_path).parts[0] for p in nested_matches}))
        raise FileNotFoundError(
            f"Multiple cases under {run_path} have a snapshot for trial {trial} turn {turn}: "
            f"{cases}. Pass --run pointing at the specific case directory."
        )
    raise FileNotFoundError(f"No eval state snapshot found at {direct}")


@contextlib.contextmanager
def _ephemeral_postgres_container() -> Iterator[str]:
    """Spin up a tmpfs-backed Postgres container and tear it down on exit.

    The container's data directory is a tmpfs mount, so the database is
    fully in-memory and disappears the moment the container stops. ``--rm``
    removes the container itself once stopped, leaving no residue.
    """
    if not shutil.which("docker"):
        raise RuntimeError(
            "docker is required to run replay without --db-uri "
            "(install docker or pass --db-uri to use an external Postgres)."
        )

    name = f"rentmate-replay-{uuid.uuid4().hex[:8]}"
    print(f"Starting ephemeral Postgres container ({_EPHEMERAL_PG_IMAGE}) as {name}...")
    subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", name,
            "-e", f"POSTGRES_USER={_EPHEMERAL_PG_USER}",
            "-e", f"POSTGRES_PASSWORD={_EPHEMERAL_PG_PASSWORD}",
            "-e", f"POSTGRES_DB={_EPHEMERAL_PG_DB}",
            "-p", "0:5432",
            "--tmpfs", "/var/lib/postgresql/data:rw",
            _EPHEMERAL_PG_IMAGE,
        ],
        check=True, capture_output=True, text=True,
    )

    try:
        port = _docker_host_port(name, 5432)
        uri = (
            f"postgresql+psycopg2://{_EPHEMERAL_PG_USER}:{_EPHEMERAL_PG_PASSWORD}"
            f"@127.0.0.1:{port}/{_EPHEMERAL_PG_DB}"
        )
        _wait_for_postgres_ready(uri, timeout=_EPHEMERAL_READY_TIMEOUT_SECONDS)
        yield uri
    finally:
        subprocess.run(
            ["docker", "stop", name],
            capture_output=True, check=False, timeout=20,
        )
        print(f"Stopped ephemeral Postgres container {name}.")


def _docker_host_port(name: str, container_port: int) -> int:
    out = subprocess.check_output(
        ["docker", "port", name, f"{container_port}/tcp"],
        text=True,
    )
    # ``docker port`` prints one mapping per line, e.g. ``0.0.0.0:32768``.
    first = out.strip().splitlines()[0]
    return int(first.rsplit(":", 1)[1])


def _wait_for_postgres_ready(uri: str, *, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            engine = create_engine(uri)
            with engine.connect() as conn:
                conn.execute(text("select 1"))
            engine.dispose()
            return
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(0.5)
    raise RuntimeError(
        f"Postgres container did not become ready within {timeout:.0f}s: {last_exc}"
    )


def _ensure_database_exists(db_uri: str) -> None:
    url = make_url(db_uri)
    if url.drivername.startswith("sqlite"):
        return
    db_name = url.database
    if not db_name:
        raise ValueError("Replay database URL must include a database name")
    admin_url = _admin_url(url)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("select 1 from pg_database where datname = :name"),
                {"name": db_name},
            ).scalar()
            if not exists:
                connection.execute(text(f'create database "{db_name}"'))
    finally:
        admin_engine.dispose()


def _admin_url(url: URL) -> URL:
    fallback = "postgres" if url.database != "postgres" else "template1"
    return url.set(database=os.getenv("RENTMATE_REPLAY_ADMIN_DB", fallback))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="Eval run folder name or path")
    parser.add_argument("--trial", type=int, default=1, help="Trial number to restore")
    parser.add_argument("--turn", type=int, required=True, help="Turn number to restore")
    parser.add_argument("--port", type=int, default=8010, help="Replay server port")
    parser.add_argument("--host", default="0.0.0.0", help="Replay server host (default 0.0.0.0 so LAN clients can reach it)")
    parser.add_argument("--db-uri", help="Existing database URI to restore into (skips the ephemeral container)")
    parser.add_argument("--env", default="development", help="RENTMATE_ENV value for the replay server")
    parser.add_argument("--log-level", default="info")
    parser.add_argument("--no-server", action="store_true", help="Restore the snapshot and print settings without starting uvicorn")
    return parser
