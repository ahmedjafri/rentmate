from pathlib import Path
import subprocess

from scripts.check_graphql_codegen import _codegen_command

def test_graphql_codegen_artifacts_are_current():
    result = subprocess.run(
        ["poetry", "run", "python", "scripts/check_graphql_codegen.py"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_codegen_command_falls_back_to_npm_exec(monkeypatch, tmp_path):
    monkeypatch.setattr("scripts.check_graphql_codegen.FRONTEND_DIR", tmp_path)
    monkeypatch.setattr("scripts.check_graphql_codegen.shutil.which", lambda name: "/usr/bin/npm" if name == "npm" else None)

    assert _codegen_command(Path("/tmp/codegen.ts")) == [
        "/usr/bin/npm",
        "exec",
        "--yes",
        "--package",
        "@graphql-codegen/cli",
        "--",
        "graphql-codegen",
        "--config",
        "/tmp/codegen.ts",
    ]
