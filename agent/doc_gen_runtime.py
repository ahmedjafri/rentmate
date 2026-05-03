from __future__ import annotations

from agent.doc_gen_loader import load_doc_gen_module

_impl = load_doc_gen_module("runtime")

SERVICE_NAME = _impl.SERVICE_NAME
METHOD_NAME = _impl.METHOD_NAME

RenderDocumentRequest = _impl.RenderDocumentRequest
RenderDocumentResult = _impl.RenderDocumentResult
DocGenClient = _impl.DocGenClient
GrpcDocGenClient = _impl.GrpcDocGenClient

subprocess = _impl.subprocess
tempfile = _impl.tempfile
os = _impl.os
sys = _impl.sys

_doc_gen_env_snapshot = _impl._doc_gen_env_snapshot


def _serialize_request(request: RenderDocumentRequest) -> bytes:
    return _impl._serialize_request(request)


def _deserialize_request(payload: bytes) -> RenderDocumentRequest:
    return _impl._deserialize_request(payload)


def _serialize_response(response: RenderDocumentResult) -> bytes:
    return _impl._serialize_response(response)


def _deserialize_response(payload: bytes) -> RenderDocumentResult:
    return _impl._deserialize_response(payload)


def _sync_impl_modules() -> None:
    _impl.subprocess = subprocess
    _impl.tempfile = tempfile
    _impl.os = os
    _impl.sys = sys
    _impl._doc_gen_env_snapshot = _doc_gen_env_snapshot


class LocalDocGenClient(DocGenClient):
    def render_document(self, request: RenderDocumentRequest) -> RenderDocumentResult:
        _sync_impl_modules()
        return _impl.LocalDocGenClient().render_document(request)


def get_doc_gen_client(config=None) -> DocGenClient:
    resolved = config
    if resolved is None:
        return _impl.get_doc_gen_client(config)
    if getattr(resolved, "doc_gen_backend", None) == getattr(_impl, "DocGenBackend").GRPC:
        return _impl.GrpcDocGenClient(resolved)
    return LocalDocGenClient()


def build_doc_gen_grpc_server(*, config=None, local_client=None):
    _sync_impl_modules()
    return _impl.build_doc_gen_grpc_server(config=config, local_client=local_client)
