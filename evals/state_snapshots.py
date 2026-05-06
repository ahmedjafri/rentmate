"""Database state snapshots for eval replay."""
from __future__ import annotations

import base64
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from db.models import Base
from evals.harness import utc_now_iso, write_json

SNAPSHOT_SCHEMA = "rentmate.eval_state_snapshot.v1"


def _encode_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return {"__type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__type": "decimal", "value": str(value)}
    if isinstance(value, UUID):
        return {"__type": "uuid", "value": str(value)}
    if isinstance(value, bytes):
        return {"__type": "bytes", "value": base64.b64encode(value).decode("ascii")}
    return value


def _decode_value(value: Any) -> Any:
    if not isinstance(value, dict) or "__type" not in value:
        return value
    kind = value.get("__type")
    raw = value.get("value")
    if raw is None:
        return None
    if kind == "datetime":
        return datetime.fromisoformat(str(raw))
    if kind == "date":
        return date.fromisoformat(str(raw))
    if kind == "decimal":
        return Decimal(str(raw))
    if kind == "uuid":
        return str(raw)
    if kind == "bytes":
        return base64.b64decode(str(raw).encode("ascii"))
    return raw


def build_state_snapshot(
    db: Session,
    *,
    case_id: str,
    trial: int,
    turn: int,
    task_id: int | str | None = None,
) -> dict[str, Any]:
    """Return a full table-level DB snapshot suitable for replay."""
    tables: list[dict[str, Any]] = []
    for table in _tables():
        rows = []
        ordering = [column.asc() for column in table.primary_key.columns]
        statement = select(table)
        if ordering:
            statement = statement.order_by(*ordering)
        for row in db.execute(statement).mappings():
            rows.append({key: _encode_value(value) for key, value in row.items()})
        tables.append({
            "name": table.name,
            "columns": [column.name for column in table.columns],
            "rows": rows,
        })

    return {
        "schema": SNAPSHOT_SCHEMA,
        "case_id": case_id,
        "trial": trial,
        "turn": turn,
        "task_id": task_id,
        "captured_at": utc_now_iso(),
        "tables": tables,
    }


def write_state_snapshot(
    db: Session,
    *,
    snapshot_dir: Path,
    case_id: str,
    trial: int,
    turn: int,
    task_id: int | str | None = None,
) -> Path:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_dir / f"turn-{turn:03d}.json"
    write_json(path, build_state_snapshot(db, case_id=case_id, trial=trial, turn=turn, task_id=task_id))
    return path


def load_state_snapshot(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text())
    if payload.get("schema") != SNAPSHOT_SCHEMA:
        raise ValueError(f"Unsupported snapshot schema in {path}: {payload.get('schema')!r}")
    return payload


def restore_state_snapshot(engine: Engine, snapshot: dict[str, Any]) -> None:
    """Replace database contents with a snapshot, preserving primary keys."""
    table_by_name = Base.metadata.tables
    Base.metadata.create_all(engine)

    with engine.begin() as connection:
        constraints_disabled = _disable_postgres_constraints(connection, engine.dialect.name)
        try:
            for table in reversed(_tables()):
                connection.execute(table.delete())

            for item in snapshot.get("tables", []):
                table = table_by_name.get(item.get("name"))
                if table is None:
                    continue
                rows = [
                    {key: _decode_value(value) for key, value in row.items()}
                    for row in item.get("rows", [])
                ]
                if rows:
                    connection.execute(insert(table), rows)
        finally:
            if constraints_disabled:
                connection.execute(text("set session_replication_role = origin"))

        if engine.dialect.name == "postgresql":
            _reset_postgres_sequences(connection)


def _tables():
    return sorted(Base.metadata.tables.values(), key=lambda table: table.name)


def _disable_postgres_constraints(connection, dialect_name: str) -> bool:
    if dialect_name != "postgresql":
        return False
    is_superuser = connection.execute(
        text("select usesuper from pg_user where usename = current_user")
    ).scalar()
    if not is_superuser:
        return False
    connection.execute(text("set session_replication_role = replica"))
    return True


def _reset_postgres_sequences(connection) -> None:
    for table in _tables():
        for column in table.primary_key.columns:
            try:
                is_int = column.type.python_type is int
            except NotImplementedError:
                is_int = False
            if not is_int:
                continue
            sequence = connection.execute(
                text("select pg_get_serial_sequence(:table_name, :column_name)"),
                {"table_name": table.name, "column_name": column.name},
            ).scalar()
            if not sequence:
                continue
            max_value = connection.execute(
                text(f'select coalesce(max("{column.name}"), 0) from "{table.name}"')
            ).scalar()
            max_int = int(max_value or 0)
            connection.execute(
                text("select setval(:sequence_name, :next_value, :is_called)"),
                {"sequence_name": sequence, "next_value": max(max_int, 1), "is_called": max_int > 0},
            )
