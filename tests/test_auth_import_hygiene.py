import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "agent" / "tools" / "__init__.py",
    ROOT / "agent" / "tools" / "_common.py",
    ROOT / "agent" / "tools" / "documents.py",
    ROOT / "agent" / "tools" / "entities.py",
    ROOT / "agent" / "tools" / "memory.py",
    ROOT / "agent" / "tools" / "messaging.py",
    ROOT / "agent" / "tools" / "onboarding.py",
    ROOT / "agent" / "tools" / "tasks.py",
    ROOT / "agent" / "tools" / "vendors.py",
    ROOT / "agent" / "context.py",
    ROOT / "handlers" / "dev.py",
    ROOT / "db" / "queries.py",
]
AUTH_NAMES = {"resolve_account_id", "resolve_org_id"}


def _function_scope_auth_imports(path: Path) -> list[tuple[str, int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    findings: list[tuple[str, int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.ImportFrom):
                continue
            if inner.module != "integrations.local_auth":
                continue
            imported = {alias.name for alias in inner.names}
            bad = imported & AUTH_NAMES
            if bad:
                findings.append((node.name, inner.lineno, ", ".join(sorted(bad))))
    return findings


def test_no_function_scope_auth_imports():
    errors = []
    for path in FILES:
        for func_name, lineno, names in _function_scope_auth_imports(path):
            errors.append(f"{path.relative_to(ROOT)}:{lineno} {func_name} imports {names} inside function scope")

    assert not errors, "Move auth resolver imports to module scope:\n" + "\n".join(errors)
