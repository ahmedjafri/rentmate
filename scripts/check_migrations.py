from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run() -> None:
    fd, db_path = tempfile.mkstemp(prefix="rentmate_migration_check_", suffix=".db")
    os.close(fd)
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db_path}"
    try:
        subprocess.run(
            ["poetry", "run", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=env,
            check=True,
        )
        subprocess.run(
            ["poetry", "run", "alembic", "check"],
            cwd=ROOT,
            env=env,
            check=True,
        )
    finally:
        Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    run()
