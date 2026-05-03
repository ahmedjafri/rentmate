from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.doc_gen_runtime import build_doc_gen_grpc_server  # noqa: E402
from integrations.runtime_config import load_runtime_config  # noqa: E402


def main() -> None:
    config = load_runtime_config()
    server, bound_port = build_doc_gen_grpc_server(config=config)
    try:
        server.start()
        print(f"doc_gen gRPC server listening on {config.doc_gen_grpc_bind} (bound port {bound_port})")
        server.wait_for_termination()
    finally:
        server.stop(grace=None)


if __name__ == "__main__":
    main()
