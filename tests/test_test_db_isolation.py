import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_DIRS = [
    ROOT / "tests",
    ROOT / "handlers" / "tests",
    ROOT / "gql" / "tests",
    ROOT / "db" / "tests",
    ROOT / "llm" / "tests",
]
HANDLER_DIR = ROOT / "handlers"


def _bad_sessionlocal_imports(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    bad_lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "db.session":
            imported = {alias.name for alias in node.names}
            if "SessionLocal" in imported:
                bad_lines.append(node.lineno)
    return bad_lines


def test_no_test_imports_live_sessionlocal():
    errors: list[str] = []
    for test_dir in TEST_DIRS:
        for path in test_dir.rglob("test_*.py"):
            for lineno in _bad_sessionlocal_imports(path):
                errors.append(
                    f"{path.relative_to(ROOT)}:{lineno} imports db.session.SessionLocal directly; "
                    "use the shared `db` fixture and patched app SessionLocal instead"
                )

    assert not errors, "\n".join(errors)


def test_handler_sessionlocal_imports_are_covered_by_isolation_fixture():
    conftest_text = (ROOT / "conftest.py").read_text()
    covered_targets = {
        "db.session.SessionLocal",
        "handlers.deps.SessionLocal",
        "handlers.chat.SessionLocal",
        "handlers.heartbeat.SessionLocal",
        "handlers.scheduler.SessionLocal",
        "main.SessionLocal",
    }

    handler_import_targets: set[str] = set()
    for path in HANDLER_DIR.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "db.session":
                imported = {alias.name for alias in node.names}
                if "SessionLocal" in imported:
                    module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
                    handler_import_targets.add(f"{module}.SessionLocal")

    missing = sorted(target for target in handler_import_targets if target not in covered_targets)
    assert not missing, (
        "SessionLocal imported in handlers without fixture patch coverage: "
        + ", ".join(missing)
        + "\nUpdate _isolate_app_sessionlocal in conftest.py when adding new handler-level SessionLocal imports."
    )
    for target in covered_targets:
        assert target in conftest_text
