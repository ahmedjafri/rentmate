#!/usr/bin/env python3
"""
agent_action.py — Agent actions tool for RentMate.

Usage:
    python agent_action.py propose_task --title X --category X [--urgency X] [--description X]
    python agent_action.py close_task --id X
    python agent_action.py set_mode --id X --mode autonomous|manual|waiting_approval
"""

import argparse
import json
import os
import sys
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))


def _make_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    _data_dir = os.getenv("RENTMATE_DATA_DIR", str(_root / "data"))
    db_path = os.getenv("RENTMATE_DB_PATH", f"{_data_dir}/rentmate.db")
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine)()


def _queue_action(action: dict):
    """Append action JSON to the workspace side-channel file."""
    workspace = os.environ.get("RENTMATE_AGENT_WORKSPACE", "")
    if not workspace:
        return
    actions_file = Path(workspace) / "pending_actions.jsonl"
    with open(actions_file, "a") as f:
        f.write(json.dumps(action) + "\n")


def main():
    parser = argparse.ArgumentParser(description="RentMate agent actions tool")
    sub = parser.add_subparsers(dest="operation", required=True)

    p_propose = sub.add_parser("propose_task")
    p_propose.add_argument("--title", required=True)
    p_propose.add_argument("--category", required=True,
                           choices=["maintenance", "lease", "leasing", "compliance", "other"])
    p_propose.add_argument("--urgency", default="medium",
                           choices=["low", "medium", "high", "critical"])
    p_propose.add_argument("--description", default="")
    p_propose.add_argument("--property-id", default=None)
    p_propose.add_argument("--task-id", default=None, help="ID of the task originating this proposal")

    p_close = sub.add_parser("close_task")
    p_close.add_argument("--id", required=True, help="Task ID to close")

    p_mode = sub.add_parser("set_mode")
    p_mode.add_argument("--id", required=True, help="Task ID")
    p_mode.add_argument("--mode", required=True,
                        choices=["autonomous", "manual", "waiting_approval"])

    args = parser.parse_args()

    try:
        if args.operation == "propose_task":
            action = {
                "action": "propose_task",
                "task_id": getattr(args, "task_id", None),
                "title": args.title,
                "category": args.category,
                "urgency": args.urgency,
                "description": args.description,
                "property_id": getattr(args, "property_id", None),
            }
            _queue_action(action)
            result = {"status": "ok", "message": f"Task proposal '{args.title}' queued for user review."}

        elif args.operation == "close_task":
            from sqlalchemy import select
            from db.models import Conversation
            db = _make_session()
            try:
                task = db.execute(
                    select(Conversation).where(
                        Conversation.id == args.id,
                        Conversation.is_task == True,  # noqa: E712
                    )
                ).scalar_one_or_none()
                if not task:
                    result = {"error": f"Task {args.id!r} not found"}
                else:
                    task.task_status = "resolved"
                    db.commit()
                    _queue_action({"action": "task_closed", "task_id": args.id})
                    result = {"status": "ok", "message": f"Task '{task.subject}' closed."}
            finally:
                db.close()

        elif args.operation == "set_mode":
            from sqlalchemy import select
            from db.models import Conversation
            db = _make_session()
            try:
                task = db.execute(
                    select(Conversation).where(
                        Conversation.id == args.id,
                        Conversation.is_task == True,  # noqa: E712
                    )
                ).scalar_one_or_none()
                if not task:
                    result = {"error": f"Task {args.id!r} not found"}
                else:
                    task.task_mode = args.mode
                    db.commit()
                    _queue_action({"action": "mode_changed", "task_id": args.id, "mode": args.mode})
                    result = {"status": "ok", "message": f"Task mode changed to '{args.mode}'."}
            finally:
                db.close()

        else:
            result = {"error": f"Unknown operation: {args.operation!r}"}

    except Exception as exc:
        result = {"error": str(exc)}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
