#!/bin/bash
# Runs the install smoke test inside Docker.
# Usage: npm run test:install
set -euo pipefail

IMAGE="rentmate-test-install"
CONTAINER="rentmate-test-install-run"
CONTEXT="$(cd "$(dirname "$0")/.." && pwd)"

cleanup() {
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "→ Building install test image..."
docker build -f "$CONTEXT/tests/Dockerfile.test-install" -t "$IMAGE" "$CONTEXT"

echo "→ Starting server..."
docker run -d --name "$CONTAINER" -p 18002:8000 "$IMAGE"

echo "→ Waiting for server to be ready (up to 60s)..."
for i in $(seq 1 60); do
    if curl -sf http://localhost:18002/health >/dev/null 2>&1; then
        echo "  ready after ${i}s"
        break
    fi
    if [[ "$i" -eq 60 ]]; then
        echo "FAIL: server did not respond within 60 seconds"
        docker logs "$CONTAINER"
        exit 1
    fi
    sleep 1
done

echo "→ Server is up — probing endpoints..."

# Health check endpoint
curl -sf http://localhost:18002/health >/dev/null \
    || { echo "FAIL: GET /health did not return 200"; exit 1; }

# FastAPI Swagger UI
curl -sf http://localhost:18002/docs >/dev/null \
    || { echo "FAIL: GET /docs did not return 200"; exit 1; }

echo ""
echo "✓ Install smoke test passed."
