import importlib
import sys

for _name in ("db", "gql", "handlers", "backends", "llm"):
    _module = importlib.import_module(_name)
    sys.modules[f"{__name__}.{_name}"] = _module

__all__ = ["app", "create_app"]


def __getattr__(name: str):
    if name in {"app", "create_app"}:
        from rentmate.app import app, create_app

        return {"app": app, "create_app": create_app}[name]
    raise AttributeError(name)
