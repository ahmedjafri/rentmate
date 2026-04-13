from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class DeploymentMode(str, Enum):
    SINGLE_MACHINE = "single-machine"
    DISTRIBUTED = "distributed"


class DocGenBackend(str, Enum):
    LOCAL = "local"
    GRPC = "grpc"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class RuntimeConfig:
    deployment_mode: DeploymentMode
    doc_gen_backend: DocGenBackend
    doc_gen_grpc_target: str
    doc_gen_grpc_bind: str
    doc_gen_grpc_insecure: bool
    doc_gen_render_timeout_ms: int


def load_runtime_config() -> RuntimeConfig:
    deployment_mode = DeploymentMode(os.getenv("RENTMATE_DEPLOYMENT_MODE", DeploymentMode.SINGLE_MACHINE.value))
    backend_override = os.getenv("RENTMATE_DOC_GEN_BACKEND", "").strip().lower()

    if backend_override:
        doc_gen_backend = DocGenBackend(backend_override)
    elif deployment_mode == DeploymentMode.DISTRIBUTED:
        doc_gen_backend = DocGenBackend.GRPC
    else:
        doc_gen_backend = DocGenBackend.LOCAL

    return RuntimeConfig(
        deployment_mode=deployment_mode,
        doc_gen_backend=doc_gen_backend,
        doc_gen_grpc_target=os.getenv("RENTMATE_DOC_GEN_GRPC_TARGET", "127.0.0.1:50061"),
        doc_gen_grpc_bind=os.getenv("RENTMATE_DOC_GEN_GRPC_BIND", "0.0.0.0:50061"),
        doc_gen_grpc_insecure=_env_bool("RENTMATE_DOC_GEN_GRPC_INSECURE", True),
        doc_gen_render_timeout_ms=int(os.getenv("RENTMATE_DOC_GEN_RENDER_TIMEOUT_MS", "30000")),
    )
