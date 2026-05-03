from __future__ import annotations

import subprocess
from dataclasses import replace

import grpc
import pytest

from agent.doc_gen_runtime import (
    GrpcDocGenClient,
    LocalDocGenClient,
    RenderDocumentRequest,
    RenderDocumentResult,
    build_doc_gen_grpc_server,
    get_doc_gen_client,
)
from integrations.runtime_config import DeploymentMode, DocGenBackend, RuntimeConfig, load_runtime_config


def _skip_if_weasyprint_native_deps_missing(error: Exception) -> None:
    if "libpangoft2" in str(error) or "WeasyPrint could not import some external libraries" in str(error):
        pytest.skip(f"WeasyPrint native dependencies unavailable: {error}")


def test_runtime_config_defaults_to_single_machine_local(monkeypatch):
    monkeypatch.delenv("RENTMATE_DEPLOYMENT_MODE", raising=False)
    monkeypatch.delenv("RENTMATE_DOC_GEN_BACKEND", raising=False)

    config = load_runtime_config()

    assert config.deployment_mode == DeploymentMode.SINGLE_MACHINE
    assert config.doc_gen_backend == DocGenBackend.LOCAL


def test_runtime_config_prefers_grpc_in_distributed_mode(monkeypatch):
    monkeypatch.setenv("RENTMATE_DEPLOYMENT_MODE", "distributed")
    monkeypatch.delenv("RENTMATE_DOC_GEN_BACKEND", raising=False)

    config = load_runtime_config()

    assert config.deployment_mode == DeploymentMode.DISTRIBUTED
    assert config.doc_gen_backend == DocGenBackend.GRPC


def test_runtime_config_allows_explicit_doc_gen_override(monkeypatch):
    monkeypatch.setenv("RENTMATE_DEPLOYMENT_MODE", "distributed")
    monkeypatch.setenv("RENTMATE_DOC_GEN_BACKEND", "local")

    config = load_runtime_config()

    assert config.doc_gen_backend == DocGenBackend.LOCAL


def test_get_doc_gen_client_chooses_grpc_when_configured():
    config = RuntimeConfig(
        deployment_mode=DeploymentMode.DISTRIBUTED,
        doc_gen_backend=DocGenBackend.GRPC,
        doc_gen_grpc_target="127.0.0.1:50061",
        doc_gen_grpc_bind="127.0.0.1:50061",
        doc_gen_grpc_insecure=True,
        doc_gen_render_timeout_ms=30000,
    )

    assert isinstance(get_doc_gen_client(config), GrpcDocGenClient)


def test_local_doc_gen_client_renders_via_subprocess(monkeypatch, tmp_path):
    client = LocalDocGenClient()
    fake_pdf = tmp_path / "rendered.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")

    def fake_named_tempfile(*args, **kwargs):
        class _Tmp:
            name = str(fake_pdf)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Tmp()

    def fake_run(cmd, input, text, capture_output, check):
        assert cmd[0]
        assert cmd[1].endswith("render_document_pdf.py")
        assert cmd[2] == str(fake_pdf)
        assert input == "<h1>hello</h1>"
        assert text is True
        assert capture_output is True
        assert check is False
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("agent.doc_gen_runtime.tempfile.NamedTemporaryFile", fake_named_tempfile)
    monkeypatch.setattr("agent.doc_gen_runtime.subprocess.run", fake_run)

    rendered = client.render_document(RenderDocumentRequest(html_document="<h1>hello</h1>"))

    assert rendered.pdf_bytes == b"%PDF-1.4 fake"
    assert rendered.renderer == "weasyprint"
    assert not fake_pdf.exists()


def test_local_doc_gen_client_surfaces_subprocess_errors(monkeypatch, tmp_path):
    client = LocalDocGenClient()
    fake_pdf = tmp_path / "rendered.pdf"

    def fake_named_tempfile(*args, **kwargs):
        class _Tmp:
            name = str(fake_pdf)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Tmp()

    def fake_run(cmd, input, text, capture_output, check):
        return subprocess.CompletedProcess(cmd, 1, "", "BrowserType.launch: TargetClosedError")

    monkeypatch.setattr("agent.doc_gen_runtime.tempfile.NamedTemporaryFile", fake_named_tempfile)
    monkeypatch.setattr("agent.doc_gen_runtime.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="document renderer subprocess failed: BrowserType.launch: TargetClosedError"):
        client.render_document(RenderDocumentRequest(html_document="<h1>hello</h1>"))


def test_local_doc_gen_client_includes_subprocess_context_on_failure(monkeypatch, tmp_path):
    client = LocalDocGenClient()
    fake_pdf = tmp_path / "rendered.pdf"

    def fake_named_tempfile(*args, **kwargs):
        class _Tmp:
            name = str(fake_pdf)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        return _Tmp()

    def fake_run(cmd, input, text, capture_output, check):
        return subprocess.CompletedProcess(
            cmd,
            1,
            "",
            "[doc_gen] {\"stage\":\"launch_browser.error\"}\nBrowserType.launch: TargetClosedError",
        )

    monkeypatch.setattr("agent.doc_gen_runtime.tempfile.NamedTemporaryFile", fake_named_tempfile)
    monkeypatch.setattr("agent.doc_gen_runtime.subprocess.run", fake_run)
    monkeypatch.setattr("agent.doc_gen_runtime.os.getcwd", lambda: "/tmp/rentmate-api")
    monkeypatch.setattr("agent.doc_gen_runtime._doc_gen_env_snapshot", lambda: {"HOME": "/home/tester", "TMPDIR": "/tmp"})

    with pytest.raises(RuntimeError) as exc_info:
        client.render_document(RenderDocumentRequest(html_document="<h1>hello</h1>"))

    message = str(exc_info.value)
    assert "document renderer subprocess failed:" in message
    assert "BrowserType.launch: TargetClosedError" in message
    assert "doc_gen subprocess context:" in message
    assert "\"cwd\": \"/tmp/rentmate-api\"" in message
    assert "\"HOME\": \"/home/tester\"" in message


def test_local_doc_gen_client_integration_renders_real_pdf():
    client = LocalDocGenClient()
    try:
        rendered = client.render_document(
            RenderDocumentRequest(
                html_document="<!DOCTYPE html><html><body><h1>Integration Smoke</h1><p>Rendered in subprocess.</p></body></html>"
            )
        )
    except RuntimeError as error:
        _skip_if_weasyprint_native_deps_missing(error)
        raise

    assert rendered.pdf_bytes.startswith(b"%PDF-")
    assert len(rendered.pdf_bytes) > 1000
    assert rendered.renderer == "weasyprint"


def test_doc_gen_grpc_roundtrip():
    class FakeLocalClient:
        def render_document(self, request: RenderDocumentRequest) -> RenderDocumentResult:
            assert request.html_document == "<h1>hello</h1>"
            return RenderDocumentResult(pdf_bytes=b"%PDF-1.4 grpc", renderer="weasyprint")

    config = RuntimeConfig(
        deployment_mode=DeploymentMode.DISTRIBUTED,
        doc_gen_backend=DocGenBackend.GRPC,
        doc_gen_grpc_target="127.0.0.1:0",
        doc_gen_grpc_bind="127.0.0.1:0",
        doc_gen_grpc_insecure=True,
        doc_gen_render_timeout_ms=30000,
    )
    server, port = build_doc_gen_grpc_server(config=config, local_client=FakeLocalClient())
    server.start()
    try:
        client = GrpcDocGenClient(replace(config, doc_gen_grpc_target=f"127.0.0.1:{port}"))
        rendered = client.render_document(RenderDocumentRequest(html_document="<h1>hello</h1>"))
    finally:
        server.stop(grace=None)

    assert rendered.pdf_bytes == b"%PDF-1.4 grpc"
    assert rendered.renderer == "weasyprint"


def test_doc_gen_grpc_reports_remote_failures():
    class FailingLocalClient:
        def render_document(self, request: RenderDocumentRequest) -> RenderDocumentResult:
            raise RuntimeError("renderer crashed")

    config = RuntimeConfig(
        deployment_mode=DeploymentMode.DISTRIBUTED,
        doc_gen_backend=DocGenBackend.GRPC,
        doc_gen_grpc_target="127.0.0.1:0",
        doc_gen_grpc_bind="127.0.0.1:0",
        doc_gen_grpc_insecure=True,
        doc_gen_render_timeout_ms=30000,
    )
    server, port = build_doc_gen_grpc_server(config=config, local_client=FailingLocalClient())
    server.start()
    try:
        client = GrpcDocGenClient(replace(config, doc_gen_grpc_target=f"127.0.0.1:{port}"))
        with pytest.raises(grpc.RpcError, match="renderer crashed"):
            client.render_document(RenderDocumentRequest(html_document="<h1>boom</h1>"))
    finally:
        server.stop(grace=None)
