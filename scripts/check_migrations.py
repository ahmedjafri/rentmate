from __future__ import annotations

import os
import subprocess
from pathlib import Path

from testcontainers.postgres import PostgresContainer

ROOT = Path(__file__).resolve().parent.parent


def run() -> None:
    with PostgresContainer("pgvector/pgvector:pg16") as pg:
        env = os.environ.copy()
        env["DATABASE_URL"] = pg.get_connection_url()
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


if __name__ == "__main__":
    run()
