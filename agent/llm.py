"""Shim that provides reconfigure() for hot-reloading LLM settings."""
from agent.registry import agent_registry


def reconfigure():
    """Restart the nanobot agent loop to pick up updated LLM env vars."""
    agent_registry.stop_gateway()
    agent_registry.start_gateway()
