"""Unit tests for llm/registry.py — AgentRegistry."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

# DEFAULT_USER_ID removed — tests use local constant
DEFAULT_USER_ID = "1"


@pytest.mark.usefixtures("db")
class TestAgentRegistry(unittest.TestCase):

    def _make_registry(self):
        """Return a fresh AgentRegistry with no agents ready."""
        from llm.registry import AgentRegistry
        registry = AgentRegistry.__new__(AgentRegistry)
        registry._lock = threading.Lock()
        registry._ready = {}
        registry._tools_registered = False
        return registry

    # ------------------------------------------------------------------
    # ensure_agent
    # ------------------------------------------------------------------

    def test_ensure_agent_returns_creator_id(self):
        registry = self._make_registry()
        with patch.object(registry, "start_gateway"), \
             patch.object(registry, "_write_workspace"):
            agent_id = registry.ensure_agent(DEFAULT_USER_ID, self.db)
        self.assertEqual(agent_id, DEFAULT_USER_ID)

    def test_ensure_agent_starts_gateway_when_not_ready(self):
        registry = self._make_registry()
        with patch.object(registry, "start_gateway") as mock_start, \
             patch.object(registry, "_write_workspace"):
            registry.ensure_agent(DEFAULT_USER_ID, self.db)
        mock_start.assert_called_once_with(DEFAULT_USER_ID)

    def test_ensure_agent_skips_start_when_already_ready(self):
        registry = self._make_registry()
        registry._ready[DEFAULT_USER_ID] = True
        with patch.object(registry, "start_gateway") as mock_start:
            registry.ensure_agent(DEFAULT_USER_ID, self.db)
        mock_start.assert_not_called()

    # ------------------------------------------------------------------
    # is_healthy / stop_gateway
    # ------------------------------------------------------------------

    def test_is_healthy_false_when_not_ready(self):
        registry = self._make_registry()
        self.assertFalse(registry.is_healthy(DEFAULT_USER_ID))

    def test_is_healthy_true_when_ready(self):
        registry = self._make_registry()
        registry._ready[DEFAULT_USER_ID] = True
        self.assertTrue(registry.is_healthy(DEFAULT_USER_ID))

    def test_stop_gateway_clears_ready(self):
        registry = self._make_registry()
        registry._ready[DEFAULT_USER_ID] = True
        registry.stop_gateway(DEFAULT_USER_ID)
        self.assertFalse(registry.is_healthy(DEFAULT_USER_ID))

    # ------------------------------------------------------------------
    # populate_all_agents — workspace files
    # ------------------------------------------------------------------

    def test_populate_all_agents_writes_core_workspace_files(self):
        from llm.registry import AgentRegistry

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "agent"
            data_dir.mkdir(parents=True)

            with patch("llm.registry.get_agent_data_dir", return_value=data_dir), patch(
                "llm.registry._lookup_account_id", return_value=int(DEFAULT_USER_ID)
            ):
                registry = AgentRegistry()
                registry.populate_all_agents(self.db)

            soul_md = data_dir / DEFAULT_USER_ID / "SOUL.md"
            self.assertTrue(soul_md.exists(), "SOUL.md should be created")
            self.assertFalse(
                (data_dir / DEFAULT_USER_ID / "USER.md").exists(),
                "USER.md should not be created",
            )

    def test_ensure_agent_runtime_dirs_creates_hermes_profile_dirs(self):
        from llm.registry import ensure_agent_runtime_dirs

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "agent"
            with patch("llm.registry.get_agent_data_dir", return_value=data_dir):
                runtime_dirs = ensure_agent_runtime_dirs(DEFAULT_USER_ID)

            self.assertTrue(runtime_dirs["workspace"].is_dir())
            self.assertTrue(runtime_dirs["hermes_home"].is_dir())
            self.assertEqual(runtime_dirs["hermes_home"], runtime_dirs["workspace"])
            self.assertTrue(runtime_dirs["working_dir"].is_dir())
            self.assertEqual(runtime_dirs["working_dir"], runtime_dirs["workspace"] / "home")

    def test_ensure_hermes_runtime_home_requires_env(self):
        from llm.registry import ensure_hermes_runtime_home

        with patch.dict("os.environ", {"HERMES_HOME": ""}, clear=False):
            with self.assertRaisesRegex(RuntimeError, "HERMES_HOME must be set"):
                ensure_hermes_runtime_home()

    def test_ensure_hermes_runtime_home_accepts_writable_path(self):
        from llm.registry import ensure_hermes_runtime_home

        with tempfile.TemporaryDirectory() as tmp:
            hermes_home = Path(tmp) / "hermes-home"
            with patch.dict("os.environ", {"HERMES_HOME": str(hermes_home)}, clear=False):
                resolved = ensure_hermes_runtime_home()

            self.assertEqual(resolved, hermes_home.resolve())
            self.assertTrue(resolved.is_dir())
