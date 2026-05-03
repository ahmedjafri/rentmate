"""Unit tests for llm/registry.py — AgentRegistry."""

import threading
import unittest
from unittest.mock import patch

import pytest

DEFAULT_USER_ID = "1"


@pytest.mark.usefixtures("db")
class TestAgentRegistry(unittest.TestCase):

    def _make_registry(self):
        """Return a fresh AgentRegistry with no agents ready."""
        from agent.registry import AgentRegistry
        registry = AgentRegistry.__new__(AgentRegistry)
        registry._lock = threading.Lock()
        registry._ready = {}
        return registry

    # ------------------------------------------------------------------
    # ensure_agent
    # ------------------------------------------------------------------

    def test_ensure_agent_returns_creator_id(self):
        registry = self._make_registry()
        with patch.object(registry, "start_gateway"):
            agent_id = registry.ensure_agent(DEFAULT_USER_ID, self.db)
        self.assertEqual(agent_id, DEFAULT_USER_ID)

    def test_ensure_agent_starts_gateway_when_not_ready(self):
        registry = self._make_registry()
        with patch.object(registry, "start_gateway") as mock_start:
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
    # build_system_prompt_bundle reads SOUL.md from the template dir
    # ------------------------------------------------------------------

    def test_build_system_prompt_bundle_includes_soul_md(self):
        registry = self._make_registry()
        bundle = registry.build_system_prompt_bundle(DEFAULT_USER_ID)
        names = [p["name"] for p in bundle["parts"]]
        self.assertIn("SOUL.md", names)
        # SOUL.md content should be present in the assembled prompt.
        self.assertIn("RentMate", bundle["system_prompt"])

    def test_build_system_prompt_substitutes_tools_placeholder(self):
        """The ``{{tools}}`` placeholder in SOUL.md should be replaced
        with the auto-generated tool list before the prompt ships."""
        registry = self._make_registry()
        bundle = registry.build_system_prompt_bundle(DEFAULT_USER_ID)
        prompt = bundle["system_prompt"]
        assert "{{tools}}" not in prompt
        # Auto-generated section headers must show up.
        assert "**Read tools**" in prompt
        assert "**Immediate tools**" in prompt
        assert "**Write tools**" in prompt
        # A few representative tool names from each category.
        assert "`lookup_vendors`" in prompt
        assert "`remember_about_entity`" in prompt
        assert "`message_person`" in prompt
