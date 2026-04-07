# Known Issues

## KI-001: Coordination order not always followed
**Symptom**: Agent sometimes confirms vendor schedule before checking with tenant.
**Root cause**: SOUL.md has the rule but DeepSeek doesn't always follow multi-step coordination instructions when there's a lot of context.
**Workaround**: Run eval multiple times — passes ~60-70% of the time.
**Fix**: Could add a structural guardrard in `message_person` tool that checks if tenant was contacted before allowing vendor confirmation in scheduling contexts. Or use a more capable model.

## KI-002: LLM judge strictness varies
**Symptom**: judge_message() sometimes fails even when the response is reasonable.
**Root cause**: DeepSeek as a judge can be overly strict on scoring criteria.
**Workaround**: Criteria worded as "does X OR Y" to give flexibility.
**Fix**: Consider using a separate, more calibrated model for judging, or a rubric with examples.

## KI-003: Tool errors in test environment
**Symptom**: update_steps, save_memory sometimes error with SQLite thread issues in tests.
**Root cause**: In-memory SQLite + session isolation doesn't perfectly replicate production.
**Workaround**: Auto-patching SessionLocal in run_turn_sync helps. Some tools still create their own sessions.
**Fix**: Need to ensure ALL code paths that create sessions use the patched version.

## KI-004: Agent verbose in AI chat
**Symptom**: Agent sometimes narrates its tool calls ("I've updated the progress steps...") in AI chat responses.
**Root cause**: SOUL.md says "never narrate tool calls" but agent interprets this as only for external messages.
**Workaround**: Not a functional issue — just noise in the AI thread.
**Fix**: Strengthen SOUL.md to say "never narrate tool calls in ANY response".
