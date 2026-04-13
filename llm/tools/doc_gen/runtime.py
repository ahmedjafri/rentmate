from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path

from backends.runtime_config import DocGenBackend, RuntimeConfig, load_runtime_config

SERVICE_NAME = "rentmate.docgen.v1.DocGen"
METHOD_NAME = f"/{SERVICE_NAME}/RenderDocument"


@dataclass(frozen=True)
class RenderDocumentRequest:
    html_document: str


@dataclass(frozen=True)
class RenderDocumentResult:
    pdf_bytes: bytes
    renderer: str


class DocGenClient:
    def render_document(self, request: RenderDocumentRequest) -> RenderDocumentResult:
        raise NotImplementedError


def _serialize_request(request: RenderDocumentRequest) -> bytes:
    return json.dumps(asdict(request)).encode("utf-8")


def _deserialize_request(payload: bytes) -> RenderDocumentRequest:
    data = json.loads(payload.decode("utf-8"))
    return RenderDocumentRequest(html_document=data["html_document"])


def _serialize_response(response: RenderDocumentResult) -> bytes:
    return json.dumps(
        {
            "pdf_bytes": base64.b64encode(response.pdf_bytes).decode("ascii"),
            "renderer": response.renderer,
        }
    ).encode("utf-8")


def _deserialize_response(payload: bytes) -> RenderDocumentResult:
    data = json.loads(payload.decode("utf-8"))
    return RenderDocumentResult(
        pdf_bytes=base64.b64decode(data["pdf_bytes"]),
        renderer=data["renderer"],
    )


def _renderer_script_path() -> Path:
    return Path(__file__).resolve().parent / "render_document_pdf.py"


def _doc_gen_env_snapshot() -> dict[str, str]:
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


class LocalDocGenClient(DocGenClient):
    def render_document(self, request: RenderDocumentRequest) -> RenderDocumentResult:
        renderer_script = _renderer_script_path()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as pdf_file:
            output_path = Path(pdf_file.name)
        try:
            completed = subprocess.run(
                [sys.executable, str(renderer_script), str(output_path)],
                input=request.html_document,
                text=True,
                capture_output=True,
                check=False,
            )
            if completed.returncode != 0:
                stderr = (completed.stderr or "").strip()
                stdout = (completed.stdout or "").strip()
                detail = stderr or stdout or f"renderer exited with status {completed.returncode}"
                context = {
                    "python": sys.executable,
                    "renderer_script": str(renderer_script),
                    "cwd": os.getcwd(),
                    "output_path": str(output_path),
                    "env": _doc_gen_env_snapshot(),
                }
                context_json = json.dumps(context, sort_keys=True)
                raise RuntimeError(
                    "document renderer subprocess failed: "
                    f"{detail}\n"
                    f"doc_gen subprocess context: {context_json}"
                )
            pdf_bytes = output_path.read_bytes()
        finally:
            output_path.unlink(missing_ok=True)
        return RenderDocumentResult(
            pdf_bytes=pdf_bytes,
            renderer="weasyprint",
        )


class GrpcDocGenClient(DocGenClient):
    def __init__(self, config: RuntimeConfig):
        self._config = config

    def render_document(self, request: RenderDocumentRequest) -> RenderDocumentResult:
        import grpc

        if not self._config.doc_gen_grpc_insecure:
            raise RuntimeError("secure doc_gen gRPC transport is not implemented")

        with grpc.insecure_channel(self._config.doc_gen_grpc_target) as channel:
            rpc = channel.unary_unary(
                METHOD_NAME,
                request_serializer=_serialize_request,
                response_deserializer=_deserialize_response,
            )
            return rpc(request, timeout=self._config.doc_gen_render_timeout_ms / 1000)


def get_doc_gen_client(config: RuntimeConfig | None = None) -> DocGenClient:
    resolved = config or load_runtime_config()
    if resolved.doc_gen_backend == DocGenBackend.GRPC:
        return GrpcDocGenClient(resolved)
    return LocalDocGenClient()


def build_doc_gen_grpc_server(
    *,
    config: RuntimeConfig | None = None,
    local_client: DocGenClient | None = None,
):
    import grpc

    resolved = config or load_runtime_config()
    client = local_client or LocalDocGenClient()
    server = grpc.server(ThreadPoolExecutor(max_workers=4))

    def handle_render(request: RenderDocumentRequest, context):
        try:
            return client.render_document(request)
        except Exception as exc:
            context.abort(grpc.StatusCode.INTERNAL, str(exc))

    generic_handler = grpc.method_handlers_generic_handler(
        SERVICE_NAME,
        {
            "RenderDocument": grpc.unary_unary_rpc_method_handler(
                handle_render,
                request_deserializer=_deserialize_request,
                response_serializer=_serialize_response,
            )
        },
    )
    server.add_generic_rpc_handlers((generic_handler,))
    bound_port = server.add_insecure_port(resolved.doc_gen_grpc_bind)
    return server, bound_port
