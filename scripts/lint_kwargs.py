#!/usr/bin/env python3
"""Lint: enforce keyword-only params on public functions in core directories.

Public functions (not prefixed with _) in db/, gql/, backends/, llm/ must use
keyword-only parameters (after *). Private functions and __init__/__new__ are exempt.

Also checks that no code imports private symbols (prefixed with _) from these
directories.

Usage:
    python scripts/lint_kwargs.py          # check all
    python scripts/lint_kwargs.py --fix    # not supported yet, just reports
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DIRS = ["db", "gql", "backends", "llm"]
EXCLUDE = {"__pycache__", "migrations", "tests", ".venv"}


def _should_check(path: Path) -> bool:
    return path.suffix == ".py" and not any(ex in path.parts for ex in EXCLUDE)


def _is_public(name: str) -> bool:
    return not name.startswith("_")


MIN_PARAMS_FOR_KWARGS = 3  # only enforce when 3+ params (after self/cls)


def check_keyword_only_params(path: Path, tree: ast.Module) -> list[str]:
    """Find public functions with 3+ positional params that should be keyword-only."""
    errors = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = node.name
        if not _is_public(name):
            continue

        args = node.args
        regular_args = args.args or []

        # Filter out self/cls
        param_names = [a.arg for a in regular_args]
        if param_names and param_names[0] in ("self", "cls"):
            param_names = param_names[1:]

        # Skip if already using keyword-only args
        if args.kwonlyargs:
            continue

        # Only enforce when there are 3+ params (after self/cls)
        if len(param_names) < MIN_PARAMS_FOR_KWARGS:
            continue

        errors.append(
            f"{path}:{node.lineno}: {name}() has {len(param_names)} positional params "
            f"(should use keyword-only after first): {', '.join(param_names)}"
        )
    return errors


def check_private_imports(path: Path, tree: ast.Module) -> list[str]:
    """Find imports of private symbols from core directories."""
    errors = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            # Check if importing from a core directory
            top_module = node.module.split(".")[0]
            if top_module not in DIRS:
                continue
            # Check for private names in the import list
            for alias in (node.names or []):
                imported_name = alias.name
                if imported_name.startswith("_") and not imported_name.startswith("__"):
                    errors.append(
                        f"{path}:{node.lineno}: imports private symbol '{imported_name}' from {node.module}"
                    )
    return errors


def main():
    all_errors: list[str] = []

    for dir_name in DIRS:
        dir_path = ROOT / dir_name
        if not dir_path.exists():
            continue
        for py_file in sorted(dir_path.rglob("*.py")):
            if not _should_check(py_file):
                continue
            rel = py_file.relative_to(ROOT)
            try:
                source = py_file.read_text()
                tree = ast.parse(source, filename=str(rel))
            except SyntaxError as e:
                print(f"SKIP {rel}: {e}")
                continue

            all_errors.extend(check_keyword_only_params(rel, tree))

    # Check private imports from ALL Python files (not just core dirs)
    for py_file in sorted(ROOT.rglob("*.py")):
        if not _should_check(py_file):
            continue
        rel = py_file.relative_to(ROOT)
        try:
            source = py_file.read_text()
            tree = ast.parse(source, filename=str(rel))
        except SyntaxError:
            continue
        all_errors.extend(check_private_imports(rel, tree))

    if all_errors:
        print(f"\n{len(all_errors)} violation(s) found:\n")
        for err in all_errors:
            print(f"  {err}")
        print()
        sys.exit(1)
    else:
        print("All checks passed!")
        sys.exit(0)


if __name__ == "__main__":
    main()
