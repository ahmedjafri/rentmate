#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="infra/docker-compose.dev.yml"

docker compose -f "$COMPOSE_FILE" up --build -d
docker compose -f "$COMPOSE_FILE" logs -f api
