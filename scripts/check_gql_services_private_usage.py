#!/usr/bin/env python3
"""Fail if code outside gql.services imports underscore-prefixed gql.services symbols."""

from __future__ import annotations

import ast
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SERVICES_DIR = ROOT / "gql" / "services"


def _iter_python_files() -> list[Path]:
    return [
        path for path in ROOT.rglob("*.py")
        if ".venv" not in path.parts and "__pycache__" not in path.parts and ".claude" not in path.parts
    ]


def _is_services_file(path: Path) -> bool:
    return SERVICES_DIR in path.parents or path == SERVICES_DIR


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def main() -> int:
    violations: list[str] = []

    for path in _iter_python_files():
        if _is_services_file(path):
            continue

        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("gql.services"):
                for alias in node.names:
                    if alias.name.startswith("_"):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno} imports private symbol "
                            f"{alias.name} from {node.module}"
                        )

    if violations:
        print("\n".join(sorted(violations)))
        return 1

    print("No private gql.services imports found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
