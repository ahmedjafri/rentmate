import os
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest
import yaml


@pytest.mark.postgres
def test_dev_compose_stack_starts_healthy(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    compose_file = tmp_path / "docker-compose.dev.yml"
    compose_data = yaml.safe_load((repo_root / "infra/docker-compose.dev.yml").read_text())
    compose_data["services"]["postgres"]["ports"] = []
    compose_data["services"]["postgres"]["volumes"] = [
        f"{repo_root / 'data/db'}:/var/lib/postgresql/data"
    ]
    compose_data["services"]["api"]["ports"] = ["0:8002"]
    compose_data["services"]["api"].pop("env_file", None)
    compose_data["services"]["api"]["build"]["context"] = str(repo_root)
    compose_data["services"]["api"]["build"]["dockerfile"] = str(repo_root / "infra/backend.Dockerfile")
    compose_data["services"]["api"]["volumes"] = [
        f"{repo_root}:/app",
        f"{repo_root / 'data'}:/app/data",
    ]
    compose_data["services"]["web"]["ports"] = []
    compose_data["services"]["web"]["build"]["context"] = str(repo_root)
    compose_data["services"]["web"]["build"]["dockerfile"] = str(repo_root / "infra/frontend.Dockerfile")
    compose_data["services"]["web"]["volumes"] = [
        f"{repo_root / 'www/rentmate-ui'}:/app/www/rentmate-ui",
        "rentmate_web_node_modules:/app/www/rentmate-ui/node_modules",
    ]
    compose_file.write_text(yaml.safe_dump(compose_data, sort_keys=False))

    project_name = f"rentmate-smoke-{uuid.uuid4().hex[:8]}"
    env = os.environ.copy()
    env["DOCKER_USER"] = f"{os.getuid()}:{os.getgid()}"

    compose_cmd = [
        "docker", "compose",
        "-p", project_name,
        "--project-directory", str(repo_root),
        "-f", str(compose_file),
    ]

    try:
        subprocess.run(
            [*compose_cmd, "up", "--build", "-d", "postgres", "api"],
            cwd=repo_root,
            env=env,
            check=True,
        )
        port_output = subprocess.check_output(
            [*compose_cmd, "port", "api", "8002"],
            cwd=repo_root,
            env=env,
            text=True,
        ).strip()
        host_port = int(port_output.rsplit(":", 1)[1])

        deadline = time.time() + 90
        last_error = None
        while time.time() < deadline:
            try:
                response = httpx.get(f"http://127.0.0.1:{host_port}/health", timeout=5)
                if response.status_code == 200:
                    payload = response.json()
                    assert payload["database"] == "connected"
                    assert payload["status"] in {"healthy", "degraded"}
                    return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
            time.sleep(2)

        raise AssertionError(f"Dev stack did not become healthy in time: {last_error}")
    finally:
        subprocess.run(
            [*compose_cmd, "down", "-v"],
            cwd=repo_root,
            env=env,
            check=False,
        )
