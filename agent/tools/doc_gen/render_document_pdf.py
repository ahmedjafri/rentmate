from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _renderer_env_snapshot() -> dict[str, str]:
    keys = [
        "HOME",
        "PATH",
        "PWD",
        "PYTHONPATH",
        "TMPDIR",
        "USER",
        "VIRTUAL_ENV",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_RUNTIME_DIR",
        "LD_LIBRARY_PATH",
    ]
    snapshot: dict[str, str] = {}
    for key in keys:
        value = os.getenv(key)
        if value:
            snapshot[key] = value
    return snapshot


def _emit_renderer_diagnostics(stage: str, **details) -> None:
    payload = {
        "stage": stage,
        **details,
    }
    print(f"[doc_gen] {json.dumps(payload, sort_keys=True)}", file=sys.stderr)


def render_pdf(*, html: str, output_path: Path) -> None:
    from weasyprint import CSS, HTML

    _emit_renderer_diagnostics(
        "render_pdf.start",
        output_path=str(output_path),
        html_length=len(html),
    )
    base_url = os.getcwd()
    HTML(string=html, base_url=base_url).write_pdf(
        target=str(output_path),
        stylesheets=[
            CSS(
                string="""
                @page {
                  size: Letter;
                  margin: 0;
                }
                """
            )
        ],
    )
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError("generated PDF is empty")


def render_pdf_bytes(*, html: str) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as pdf_file:
        output_path = Path(pdf_file.name)
    try:
        render_pdf(html=html, output_path=output_path)
        return output_path.read_bytes()
    finally:
        output_path.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python render_document_pdf.py <output.pdf>", file=sys.stderr)
        raise SystemExit(2)

    output_path = Path(sys.argv[1])
    html = sys.stdin.read()
    _emit_renderer_diagnostics(
        "main.start",
        argv=sys.argv,
        cwd=os.getcwd(),
        pid=os.getpid(),
        output_path=str(output_path),
        html_length=len(html),
        env=_renderer_env_snapshot(),
    )
    render_pdf(html=html, output_path=output_path)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
