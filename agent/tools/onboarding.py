"""Onboarding tool: step progress and dismissal."""
import json
from typing import Any

from agent.tools._common import Tool


class UpdateOnboardingTool(Tool):
    """Mark onboarding steps done or dismiss onboarding entirely."""

    @property
    def name(self) -> str:
        return "update_onboarding"

    @property
    def description(self) -> str:
        return (
            "Update onboarding progress. Either mark a specific step as done "
            "(add_property, upload_document, tell_concerns) or dismiss onboarding "
            "entirely when the user wants to skip."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "step": {
                    "type": "string",
                    "enum": ["add_property", "upload_document", "tell_concerns"],
                    "description": "The step to mark as done",
                },
                "dismiss": {
                    "type": "boolean",
                    "description": "Set to true to dismiss onboarding entirely",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        from agent.tools._common import tool_session
        from agent.tracing import log_trace
        from services.settings_service import (
            dismiss_onboarding,
            get_onboarding_state,
            update_onboarding_step,
        )

        step = kwargs.get("step")
        dismiss = kwargs.get("dismiss", False)

        try:
            with tool_session() as db:
                if dismiss:
                    state = dismiss_onboarding(db)
                else:
                    if not step:
                        return json.dumps({"status": "ok", "message": "No action taken."})
                    existing = get_onboarding_state(db)
                    if not existing or existing.get("status") != "active":
                        return json.dumps({"status": "ok", "message": "Onboarding is not active."})
                    update_onboarding_step(db, step=step)
                    state = {"step": step}
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

        if dismiss:
            log_trace("onboarding", "tool", "Onboarding dismissed by agent", detail=state)
            return json.dumps({"status": "ok", "message": "Onboarding dismissed."})
        log_trace("onboarding", "tool", f"Step '{step}' marked done", detail={"step": step})
        return json.dumps({"status": "ok", "message": f"Step '{step}' marked as done."})


__all__ = ["UpdateOnboardingTool"]
