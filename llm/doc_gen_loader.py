from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=None)
def load_doc_gen_module(module_name: str):
    module_path = Path(__file__).resolve().parent / "tools" / "doc_gen" / f"{module_name}.py"
    qualified_name = f"rentmate_doc_gen_{module_name}"
    spec = importlib.util.spec_from_file_location(qualified_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load doc_gen module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    spec.loader.exec_module(module)
    return module
