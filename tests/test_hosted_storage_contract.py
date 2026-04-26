import os
from pathlib import Path
from unittest.mock import patch

import pytest

from backends.local_storage import LocalStorageBackend


def test_local_storage_uses_local_filesystem_outside_hosted_cloud_run(tmp_path):
    data_dir = tmp_path / "data"
    docs_dir = data_dir / "documents"
    with patch.dict(
        os.environ,
        {
            "RENTMATE_DATA_DIR": str(data_dir),
            "RENTMATE_DOCS_DIR": str(docs_dir),
            "RENTMATE_REQUIRED_DATA_DIR": "",
            "RENTMATE_REQUIRED_DOCS_DIR": "",
        },
        clear=False,
    ):
        backend = LocalStorageBackend()

    assert backend.docs_dir == docs_dir
    assert docs_dir.is_dir()


def test_local_storage_rejects_non_matching_required_paths(tmp_path):
    data_dir = tmp_path / "data"
    docs_dir = data_dir / "documents"
    required_data_dir = tmp_path / "mounted"
    required_docs_dir = required_data_dir / "documents"
    with patch.dict(
        os.environ,
        {
            "RENTMATE_DATA_DIR": str(data_dir),
            "RENTMATE_DOCS_DIR": str(docs_dir),
            "RENTMATE_REQUIRED_DATA_DIR": str(required_data_dir),
            "RENTMATE_REQUIRED_DOCS_DIR": str(required_docs_dir),
        },
        clear=False,
    ):
        with pytest.raises(RuntimeError, match="RENTMATE_DATA_DIR must be set"):
            LocalStorageBackend()


def test_local_storage_rejects_missing_required_runtime_path(tmp_path):
    required_data_dir = tmp_path / "mounted"
    required_docs_dir = required_data_dir / "documents"
    with patch.dict(
        os.environ,
        {
            "RENTMATE_DATA_DIR": str(required_data_dir),
            "RENTMATE_DOCS_DIR": str(required_docs_dir),
            "RENTMATE_REQUIRED_DATA_DIR": str(required_data_dir),
            "RENTMATE_REQUIRED_DOCS_DIR": str(required_docs_dir),
        },
        clear=False,
    ):
        with pytest.raises(RuntimeError, match="Runtime storage path is missing"):
            LocalStorageBackend()


def test_local_storage_accepts_valid_required_paths(tmp_path):
    required_data_dir = tmp_path / "mounted"
    required_docs_dir = required_data_dir / "documents"
    required_data_dir.mkdir(parents=True)
    with patch.dict(
        os.environ,
        {
            "RENTMATE_DATA_DIR": str(required_data_dir),
            "RENTMATE_DOCS_DIR": str(required_docs_dir),
            "RENTMATE_REQUIRED_DATA_DIR": str(required_data_dir),
            "RENTMATE_REQUIRED_DOCS_DIR": str(required_docs_dir),
        },
        clear=False,
    ):
        backend = LocalStorageBackend()

    assert backend.docs_dir == required_docs_dir
    assert required_docs_dir.is_dir()


def test_agent_data_dir_rejects_missing_required_runtime_path(tmp_path):
    required_data_dir = tmp_path / "mounted"
    required_docs_dir = required_data_dir / "documents"
    with patch.dict(
        os.environ,
        {
            "RENTMATE_DATA_DIR": str(required_data_dir),
            "RENTMATE_DOCS_DIR": str(required_docs_dir),
            "RENTMATE_REQUIRED_DATA_DIR": str(required_data_dir),
            "RENTMATE_REQUIRED_DOCS_DIR": str(required_docs_dir),
        },
        clear=False,
    ):
        from llm.registry import get_agent_data_dir

        with pytest.raises(RuntimeError, match="Runtime storage path is missing"):
            get_agent_data_dir()


def test_agent_data_dir_uses_valid_required_paths(tmp_path):
    required_data_dir = tmp_path / "mounted"
    required_docs_dir = required_data_dir / "documents"
    required_data_dir.mkdir(parents=True)
    with patch.dict(
        os.environ,
        {
            "RENTMATE_DATA_DIR": str(required_data_dir),
            "RENTMATE_DOCS_DIR": str(required_docs_dir),
            "RENTMATE_REQUIRED_DATA_DIR": str(required_data_dir),
            "RENTMATE_REQUIRED_DOCS_DIR": str(required_docs_dir),
        },
        clear=False,
    ):
        from llm.registry import get_agent_data_dir, get_agent_workspace

        data_dir = get_agent_data_dir()
        workspace = get_agent_workspace("123")

    assert data_dir == required_data_dir / "agent"
    assert workspace == Path(required_data_dir / "agent" / "123").resolve()
