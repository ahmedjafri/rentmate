#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="infra/docker-compose.dev.yml"

export DOCKER_USER="$(id -u):$(id -g)"
mkdir -p data/db data/documents data/home data/hermes
docker compose -f "$COMPOSE_FILE" up --build -d
docker compose -f "$COMPOSE_FILE" logs -f api
