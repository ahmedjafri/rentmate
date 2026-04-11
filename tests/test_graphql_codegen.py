import subprocess


def test_graphql_codegen_artifacts_are_current():
    result = subprocess.run(
        ["poetry", "run", "python", "scripts/check_graphql_codegen.py"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
