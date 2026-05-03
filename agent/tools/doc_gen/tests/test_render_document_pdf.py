import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "render_document_pdf.py"
SPEC = importlib.util.spec_from_file_location("rentmate_doc_gen_renderer", MODULE_PATH)
renderer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(renderer)


def _skip_if_weasyprint_native_deps_missing(error: Exception) -> None:
    if "libpangoft2" in str(error) or "WeasyPrint could not import some external libraries" in str(error):
        pytest.skip(f"WeasyPrint native dependencies unavailable: {error}")


def test_render_pdf_uses_weasyprint_and_writes_pdf(monkeypatch, tmp_path):
    output_path = tmp_path / "out.pdf"
    calls: dict[str, object] = {}

    class FakeHTML:
        def __init__(self, *, string, base_url):
            calls["html"] = {"string": string, "base_url": base_url}

        def write_pdf(self, *, target, stylesheets):
            calls["write_pdf"] = {"target": target, "stylesheets": stylesheets}
            Path(target).write_bytes(b"%PDF-1.4 fake")

    class FakeCSS:
        def __init__(self, *, string):
            calls["css"] = string

    monkeypatch.setitem(
        __import__("sys").modules,
        "weasyprint",
        SimpleNamespace(HTML=FakeHTML, CSS=FakeCSS),
    )

    renderer.render_pdf(html="<h1>Smoke</h1>", output_path=output_path)

    assert output_path.read_bytes().startswith(b"%PDF-1.4")
    assert calls["html"] == {"string": "<h1>Smoke</h1>", "base_url": str(Path.cwd())}
    assert calls["write_pdf"]["target"] == str(output_path)
    assert len(calls["write_pdf"]["stylesheets"]) == 1
    assert "@page" in calls["css"]


def test_render_pdf_raises_when_pdf_is_empty(monkeypatch, tmp_path):
    output_path = tmp_path / "out.pdf"

    class FakeHTML:
        def __init__(self, *, string, base_url):
            pass

        def write_pdf(self, *, target, stylesheets):
            Path(target).write_bytes(b"")

    class FakeCSS:
        def __init__(self, *, string):
            pass

    monkeypatch.setitem(
        __import__("sys").modules,
        "weasyprint",
        SimpleNamespace(HTML=FakeHTML, CSS=FakeCSS),
    )

    with pytest.raises(RuntimeError, match="generated PDF is empty"):
        renderer.render_pdf(html="<h1>Smoke</h1>", output_path=output_path)


def test_render_pdf_bytes_integration_with_real_weasyprint():
    html = "<!DOCTYPE html><html><body><h1>Integration Smoke</h1><p>Rendered via WeasyPrint.</p></body></html>"
    try:
        pdf_bytes = renderer.render_pdf_bytes(html=html)
    except OSError as error:
        _skip_if_weasyprint_native_deps_missing(error)
        raise

    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 1000
