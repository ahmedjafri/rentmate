#!/usr/bin/env python3
"""Auto-fix: add keyword-only separator (*) to public functions in core dirs.

Transforms:
    def foo(a, b, c):  →  def foo(*, a, b, c):
    def foo(self, a):  →  def foo(self, *, a):

Skips: private functions (_prefixed), __init__, __new__, functions that
already have * or **kwargs only.
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
DIRS = ["db", "gql", "backends", "llm"]
EXCLUDE = {"__pycache__", "migrations", "tests", ".venv"}


def _should_check(path: Path) -> bool:
    return path.suffix == ".py" and not any(ex in path.parts for ex in EXCLUDE)


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def fix_file(path: Path) -> int:
    source = path.read_text()
    lines = source.split("\n")
    tree = ast.parse(source, filename=str(path))

    fixes = []  # (line_number, col_offset, function_name, has_self)

    MIN_PARAMS = 3

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_public(node.name):
            continue

        args = node.args
        regular_args = args.args or []

        # Already has keyword-only separator
        if args.kwonlyargs:
            continue

        # Filter self/cls
        has_self = False
        param_names = [a.arg for a in regular_args]
        if param_names and param_names[0] in ("self", "cls"):
            has_self = True
            remaining = param_names[1:]
        else:
            remaining = param_names

        # Only enforce for 3+ params
        if len(remaining) < MIN_PARAMS:
            continue

        fixes.append((node.lineno, node.name, has_self, remaining[0]))

    if not fixes:
        return 0

    # Apply fixes from bottom to top so line numbers stay valid
    fixes.sort(key=lambda x: x[0], reverse=True)

    fixed = 0
    for lineno, func_name, has_self, first_param in fixes:
        # Find the def line (might span multiple lines)
        idx = lineno - 1
        sig_lines = []
        paren_depth = 0
        found_open = False
        for i in range(idx, min(idx + 20, len(lines))):
            line = lines[i]
            sig_lines.append((i, line))
            paren_depth += line.count("(") - line.count(")")
            if "(" in line:
                found_open = True
            if found_open and paren_depth <= 0:
                break

        full_sig = "\n".join(l for _, l in sig_lines)

        # Insert *, after the first non-self param
        # e.g. def foo(self, db, x, y) → def foo(self, db, *, x, y)
        # e.g. def foo(db, x, y) → def foo(db, *, x, y)
        # Find "first_param," and insert "*, " after it
        pattern = rf'(\b{re.escape(first_param)}\b\s*(?::[^,)]+?)?\s*,\s*)'
        new_sig = re.sub(pattern, lambda m: m.group(0) + "*, ", full_sig, count=1)

        if new_sig == full_sig:
            continue

        new_lines = new_sig.split("\n")
        for j, (line_idx, _) in enumerate(sig_lines):
            if j < len(new_lines):
                lines[line_idx] = new_lines[j]

        fixed += 1

    if fixed:
        path.write_text("\n".join(lines))

    return fixed


def main():
    total = 0
    dry_run = "--dry-run" in sys.argv

    for dir_name in DIRS:
        dir_path = ROOT / dir_name
        if not dir_path.exists():
            continue
        for py_file in sorted(dir_path.rglob("*.py")):
            if not _should_check(py_file):
                continue
            rel = py_file.relative_to(ROOT)

            if dry_run:
                source = py_file.read_text()
                tree = ast.parse(source)
                count = 0
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if _is_public(node.name) and not node.args.kwonlyargs:
                            args = node.args.args or []
                            params = [a.arg for a in args]
                            if params and params[0] in ("self", "cls"):
                                params = params[1:]
                            if len(params) >= 3:
                                count += 1
                if count:
                    print(f"  {rel}: {count} function(s) to fix")
                    total += count
            else:
                n = fix_file(py_file)
                if n:
                    print(f"  Fixed {n} function(s) in {rel}")
                    total += n

    action = "would fix" if dry_run else "fixed"
    print(f"\n{action} {total} function(s)")


if __name__ == "__main__":
    main()
