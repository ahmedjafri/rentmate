"""Unit tests for llm/registry.py — AgentRegistry (nanobot implementation)."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backends.local_auth import DEFAULT_USER_ID


@pytest.mark.usefixtures("db")
class TestAgentRegistry(unittest.TestCase):

    def _make_registry(self):
        """Return a fresh AgentRegistry with _loop=None (no real nanobot started)."""
        from llm.registry import AgentRegistry
        registry = AgentRegistry.__new__(AgentRegistry)
        registry._lock = threading.Lock()
        registry._loop = None
        return registry

    # ------------------------------------------------------------------
    # ensure_agent
    # ------------------------------------------------------------------

    def test_ensure_agent_returns_default_user_id(self):
        """ensure_agent always returns DEFAULT_USER_ID."""
        registry = self._make_registry()
        with patch.object(registry, "start_gateway"):
            agent_id = registry.ensure_agent(DEFAULT_USER_ID, self.db)
        self.assertEqual(agent_id, DEFAULT_USER_ID)

    def test_ensure_agent_starts_gateway_when_loop_is_none(self):
        """ensure_agent calls start_gateway when no loop is running."""
        registry = self._make_registry()
        with patch.object(registry, "start_gateway") as mock_start:
            registry.ensure_agent(DEFAULT_USER_ID, self.db)
        mock_start.assert_called_once()

    def test_ensure_agent_skips_start_when_loop_exists(self):
        """ensure_agent does not restart the gateway if already running."""
        registry = self._make_registry()
        registry._loop = MagicMock()  # simulate running loop
        with patch.object(registry, "start_gateway") as mock_start:
            registry.ensure_agent(DEFAULT_USER_ID, self.db)
        mock_start.assert_not_called()

    # ------------------------------------------------------------------
    # is_healthy / stop_gateway
    # ------------------------------------------------------------------

    def test_is_healthy_false_when_no_loop(self):
        registry = self._make_registry()
        self.assertFalse(registry.is_healthy())

    def test_is_healthy_true_when_loop_present(self):
        registry = self._make_registry()
        registry._loop = MagicMock()
        self.assertTrue(registry.is_healthy())

    def test_stop_gateway_clears_loop(self):
        registry = self._make_registry()
        registry._loop = MagicMock()
        registry.stop_gateway()
        self.assertIsNone(registry.get_loop())

    # ------------------------------------------------------------------
    # populate_all_agents — workspace files
    # ------------------------------------------------------------------

    def test_populate_all_agents_writes_tools_md(self):
        """populate_all_agents creates TOOLS.md in the agent workspace."""
        from llm.registry import AgentRegistry

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            data_dir = tmp_path / "agent"
            data_dir.mkdir(parents=True)

            with patch("llm.registry.DATA_DIR", data_dir):
                registry = AgentRegistry()
                registry.populate_all_agents(self.db)

            tools_md = data_dir / DEFAULT_USER_ID / "TOOLS.md"
            self.assertTrue(tools_md.exists(), "TOOLS.md should be created")
            self.assertIn("agent_data.py", tools_md.read_text())


if __name__ == "__main__":
    unittest.main()
