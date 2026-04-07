#!/usr/bin/env python3
"""Auto-fix: move lazy imports to the top of the file, with safety checks.

For each file, moves lazy imports (inside functions) to the top-level import
block. Skips imports that:
- Are for optional/external modules (google, anthropic, etc.)
- Are inside try/except blocks (conditional availability)
- Would create circular imports (verified by test-importing after each fix)

Usage:
    python scripts/fix_lazy_imports.py                 # fix all
    python scripts/fix_lazy_imports.py --dry-run       # show what would change
    python scripts/fix_lazy_imports.py handlers/chat.py  # fix one file
"""
import ast
import importlib
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
DIRS = ["db", "gql", "backends", "llm", "handlers"]
EXCLUDE = {"__pycache__", "migrations", "tests", ".venv"}

# Modules that may not be installed — must stay as lazy/conditional imports
OPTIONAL_MODULES = {
    "anthropic", "google", "google.oauth2", "google.auth",
    "googleapiclient", "run_agent", "telegram", "whatsapp",
}


def _should_check(path: Path) -> bool:
    return path.suffix == ".py" and not any(ex in path.parts for ex in EXCLUDE)


def _is_optional_import(node):
    if isinstance(node, ast.ImportFrom) and node.module:
        top = node.module.split(".")[0]
        return top in OPTIONAL_MODULES or node.module in OPTIONAL_MODULES
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name.split(".")[0] in OPTIONAL_MODULES:
                return True
    return False


def _is_inside_try_body(lineno, source_lines):
    """Heuristic: check if the import line is inside a try block."""
    for i in range(lineno - 2, max(lineno - 10, -1), -1):
        if i < 0:
            break
        stripped = source_lines[i].strip()
        if stripped == "try:":
            return True
        if stripped and not stripped.startswith("#") and not stripped.startswith("from ") and not stripped.startswith("import "):
            break
    return False


def _reconstruct_import(node):
    if isinstance(node, ast.Import):
        parts = []
        for alias in node.names:
            parts.append(f"{alias.name} as {alias.asname}" if alias.asname else alias.name)
        return f"import {', '.join(parts)}"
    else:
        names = []
        for alias in node.names:
            names.append(f"{alias.name} as {alias.asname}" if alias.asname else alias.name)
        return f"from {node.module or ''} import {', '.join(names)}"


def _verify_file(path: Path) -> bool:
    """Check if a file can be imported without errors."""
    result = subprocess.run(
        [sys.executable, "-c", f"import ast; ast.parse(open('{path}').read())"],
        capture_output=True, timeout=5,
    )
    if result.returncode != 0:
        return False
    # Also try actual import
    rel = path.relative_to(ROOT)
    module = str(rel).replace("/", ".").replace(".py", "")
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            capture_output=True, timeout=30, cwd=str(ROOT),
        )
        return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def fix_file(path: Path, *, dry_run: bool = False) -> int:
    source = path.read_text()
    lines = source.split("\n")
    tree = ast.parse(source, filename=str(path))

    # Collect top-level imports (to avoid duplicates)
    existing_imports = set()
    last_import_line = 0
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            existing_imports.add(_reconstruct_import(node))
            last_import_line = max(last_import_line, node.end_lineno or node.lineno)

    # Find lazy imports inside functions
    lazy_imports = []  # (lineno, end_lineno, import_str, node)
    for func_node in ast.walk(tree):
        if not isinstance(func_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func_node):
            if node is func_node:
                continue
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if _is_optional_import(node):
                continue
            if _is_inside_try_body(node.lineno, lines):
                continue
            imp_str = _reconstruct_import(node)
            if imp_str not in existing_imports:
                lazy_imports.append((
                    node.lineno,
                    node.end_lineno or node.lineno,
                    imp_str,
                ))

    # Deduplicate
    seen = set()
    unique_imports = []
    lines_to_remove = set()
    for lineno, end_lineno, imp_str in lazy_imports:
        for ln in range(lineno, end_lineno + 1):
            lines_to_remove.add(ln - 1)  # 0-indexed
        # Remove trailing blank line after import
        next_ln = end_lineno  # 0-indexed = end_lineno - 1 + 1
        if next_ln < len(lines) and lines[next_ln].strip() == "":
            lines_to_remove.add(next_ln)
        if imp_str not in seen:
            seen.add(imp_str)
            unique_imports.append(imp_str)

    if not unique_imports:
        return 0

    if dry_run:
        return len(unique_imports)

    # Build new file: insert imports at last_import_line, remove lazy lines
    new_lines = []
    for i in range(last_import_line):
        if i not in lines_to_remove:
            new_lines.append(lines[i])

    for imp in sorted(unique_imports):
        new_lines.append(imp)

    for i in range(last_import_line, len(lines)):
        if i not in lines_to_remove:
            new_lines.append(lines[i])

    # Write and verify
    backup = source
    path.write_text("\n".join(new_lines))

    if not _verify_file(path):
        # Revert — circular import or syntax error
        path.write_text(backup)
        rel = path.relative_to(ROOT)
        print(f"  ⚠ {rel}: reverted — import caused circular dependency or error")
        return 0

    return len(unique_imports)


def main():
    dry_run = "--dry-run" in sys.argv
    targets = [a for a in sys.argv[1:] if not a.startswith("--")]

    total = 0

    if targets:
        for target in targets:
            path = ROOT / target
            if not path.exists():
                print(f"  SKIP {target}: not found")
                continue
            n = fix_file(path, dry_run=dry_run)
            if n:
                action = "would add" if dry_run else "moved"
                rel = path.relative_to(ROOT)
                print(f"  {rel}: {action} {n} import(s) to top")
                total += n
    else:
        for dir_name in DIRS:
            dir_path = ROOT / dir_name
            if not dir_path.exists():
                continue
            for py_file in sorted(dir_path.rglob("*.py")):
                if not _should_check(py_file):
                    continue
                rel = py_file.relative_to(ROOT)
                n = fix_file(py_file, dry_run=dry_run)
                if n:
                    action = "would add" if dry_run else "moved"
                    print(f"  {rel}: {action} {n} import(s) to top")
                    total += n

    action = "would move" if dry_run else "moved"
    print(f"\n{action} {total} import(s) total")


if __name__ == "__main__":
    main()
