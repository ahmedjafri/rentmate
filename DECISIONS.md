# Decisions Log

Assumptions and design decisions made during overnight eval work.

## D001: Eval format — YAML scenarios
**Decision**: Use YAML for new scenario files (more readable than JSON for multi-line prompts and criteria).
**Rationale**: The existing JSON cases are simple. New scenarios need multi-turn flows, detailed criteria, and initial state setup — YAML handles this better.

## D002: Eval runner — pytest-based, not standalone
**Decision**: Build the new eval runner as pytest tests marked with `@pytest.mark.eval`, not a standalone script.
**Rationale**: The existing test_garage_door.py pattern works well. Pytest gives us fixtures, parallel execution, and CI integration for free. The existing runner.py is for the simple vendor outreach cases only.

## D003: LLM judge model — use same model as agent
**Decision**: Use the configured LLM_MODEL (DeepSeek) for judging, not a separate model.
**Rationale**: Simpler config. The judge prompts are structured enough that DeepSeek handles them well (proven in test_garage_door.py).

## D004: Scenario categories match SOUL.md responsibilities
**Decision**: Organize scenarios by the categories the user specified, mapping to SOUL.md's responsibilities and tool capabilities.
**Rationale**: This ensures coverage of what the agent is supposed to handle.
