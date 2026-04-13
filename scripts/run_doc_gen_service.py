from __future__ import annotations

from backends.runtime_config import load_runtime_config
from llm.doc_gen_runtime import build_doc_gen_grpc_server


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
