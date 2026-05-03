import subprocess
import sys
from pathlib import Path


def test_no_private_services_imports():
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "scripts/check_services_private_usage.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout or result.stderr
