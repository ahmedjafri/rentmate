#!/usr/bin/env python3
"""
agent_action.py — Agent actions tool for RentMate.

Usage:
    python agent_action.py propose_task --title X --category X [--urgency X] [--description X]
    python agent_action.py close_task --id X
"""

import argparse
import json
import os
import sys
from pathlib import Path

_root = Path(__file__).parent.parent
sys.path.insert(0, str(_root))

from db.enums import TaskCategory, Urgency


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
                           choices=[c.value for c in TaskCategory])
    p_propose.add_argument("--urgency", default=Urgency.MEDIUM.value,
                           choices=[u.value for u in Urgency])
    p_propose.add_argument("--description", default="")
    p_propose.add_argument("--property-id", default=None)
    p_propose.add_argument("--task-id", default=None, help="ID of the task originating this proposal")

    p_close = sub.add_parser("close_task")
    p_close.add_argument("--id", required=True, help="Task ID to close")

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
            # Queue for human confirmation — do NOT write to DB directly.
            _queue_action({"action": "close_task_proposed", "task_id": args.id})
            result = {"status": "ok", "message": "Close request queued for manager confirmation."}

        else:
            result = {"error": f"Unknown operation: {args.operation!r}"}

    except Exception as exc:
        result = {"error": str(exc)}

    print(json.dumps(result))


if __name__ == "__main__":
    main()
