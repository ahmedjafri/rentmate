#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="infra/docker-compose.dev.yml"

docker compose -f "$COMPOSE_FILE" up -d api >/dev/null

docker compose -f "$COMPOSE_FILE" exec -T api bash -lc '
python3 - <<"PY"
import importlib.util
from pathlib import Path

html = """<!DOCTYPE html>
<html>
  <body>
    <h1>Container DocGen Smoke</h1>
    <p>Rendered by WeasyPrint inside the api container.</p>
  </body>
</html>
"""

module_path = Path("/app/agent/tools/doc_gen/render_document_pdf.py")
spec = importlib.util.spec_from_file_location("rentmate_doc_gen_renderer", module_path)
renderer = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(renderer)

p = Path("/tmp/docgen-smoke.pdf")
data = renderer.render_pdf_bytes(html=html)
p.write_bytes(data)
assert p.exists(), "PDF was not created"
assert data.startswith(b"%PDF-"), f"unexpected PDF header: {data[:8]!r}"
assert len(data) > 1000, f"PDF too small: {len(data)} bytes"
print("doc-gen container smoke passed", len(data))
PY
'
