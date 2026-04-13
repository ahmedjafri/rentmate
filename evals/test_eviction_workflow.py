"""Eval: eviction workflow stays in one task and escalates deliberately."""

import pytest

from evals.conftest import get_suggestions, judge_message, run_turn_sync


@pytest.mark.eval
class TestEvictionWorkflow:
    def _build_eviction_scenario(self, scenario_builder):
        sb = scenario_builder
        prop = sb.add_property(name="1234 Acme Lane", address="1234 Acme Lane")
        unit = sb.add_unit(label="Main", prop=prop)
        tenant = sb.add_tenant(first_name="Bob", last_name="Ferguson")
        lease = sb.add_lease(
            tenant=tenant,
            unit=unit,
            prop=prop,
            rent_amount=2795.0,
            payment_status="overdue",
        )
        task = sb.add_task(
            title="Eviction proceedings for Bob Ferguson - Non-payment of rent",
            category="compliance",
            urgency="high",
            context_body=(
                "Bob Ferguson at 1234 Acme Lane, Unit Main has not paid rent for February and "
                "March 2026. Monthly rent is $2,795. Total unpaid rent is $5,590."
            ),
        )
        return {"property": prop, "unit": unit, "tenant": tenant, "lease": lease, "task": task}

    def test_asks_before_creating_upload_request_suggestion(self, db, scenario_builder, mock_sms, autonomous_mode):
        scenario = self._build_eviction_scenario(scenario_builder)

        result = run_turn_sync(
            db,
            scenario["task"],
            "Don't create a new task. Let's just use this one. He hasn't paid for February and March. What do I need to do next?",
        )

        reply = result["reply"]
        judge = judge_message(reply, "Eviction workflow first turn", [
            "Explains that a 14-day pay or vacate notice is needed before filing eviction",
            "Keeps the work in the current task instead of proposing a new task",
            "Asks the manager before creating a suggestion for the notice or upload step",
            "Makes clear what deliverable is needed from the manager",
        ])
        assert judge["pass"], f"LLM judge failed: {judge.get('reason', '')}\nReply: {reply}"
        assert get_suggestions(db, scenario["task"].id) == []

    def test_walks_full_eviction_process_through_notice_and_lawyer(self, db, scenario_builder, mock_sms, autonomous_mode):
        scenario = self._build_eviction_scenario(scenario_builder)

        first = run_turn_sync(
            db,
            scenario["task"],
            "He hasn't paid for February and March. What do I need to do next?",
        )
        assert "14-day" in first["reply"].lower()

        second = run_turn_sync(
            db,
            scenario["task"],
            "Yes, create a suggestion for me to upload the 14-day notice.",
        )
        suggestions = get_suggestions(db, scenario["task"].id)
        assert len(suggestions) == 1
        assert suggestions[0].action_payload["action"] == "request_file_upload"
        assert suggestions[0].task_id == scenario["task"].id

        third = run_turn_sync(
            db,
            scenario["task"],
            "I uploaded the signed 14-day pay or vacate notice and served it by certified mail and posting on the property today.",
        )
        third_judge = judge_message(third["reply"], "Notice uploaded and served", [
            "Acknowledges that the notice has been prepared and served",
            "Tells the manager to document the service date or method",
            "States that the next step is to wait the 14-day notice period",
            "Does not restart the workflow as a new task",
        ])
        assert third_judge["pass"], f"LLM judge failed: {third_judge.get('reason', '')}\nReply: {third['reply']}"

        fourth = run_turn_sync(
            db,
            scenario["task"],
            "The 14 days passed and Bob still has not paid or moved out. Coordinate with our lawyer on the filing steps.",
        )
        fourth_judge = judge_message(fourth["reply"], "Post-notice eviction filing", [
            "Explains that the next phase is court filing or unlawful detainer preparation",
            "Mentions coordinating with a lawyer or counsel",
            "Identifies key readiness items such as proof of service, amounts owed, or notice documentation",
            "Keeps the legal process in the current task rather than creating a separate task automatically",
        ])
        assert fourth_judge["pass"], f"LLM judge failed: {fourth_judge.get('reason', '')}\nReply: {fourth['reply']}"
