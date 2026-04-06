"""Unit tests for llm/registry.py — AgentRegistry (Hermes Agent implementation)."""

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

    def test_ensure_agent_returns_account_id(self):
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
        self.assertFalse(registry.is_healthy())

    def test_is_healthy_true_when_ready(self):
        registry = self._make_registry()
        registry._ready[DEFAULT_USER_ID] = True
        self.assertTrue(registry.is_healthy())

    def test_stop_gateway_clears_ready(self):
        registry = self._make_registry()
        registry._ready[DEFAULT_USER_ID] = True
        registry.stop_gateway()
        self.assertFalse(registry.is_healthy())

    # ------------------------------------------------------------------
    # populate_all_agents — workspace files
    # ------------------------------------------------------------------

    def test_populate_all_agents_writes_tools_md(self):
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
            content = tools_md.read_text()
            self.assertIn("Data Operations", content)
