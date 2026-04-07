# Overnight Log

## 2026-04-07T05:00 — Phase 1: Orient
- Surveyed full codebase architecture
- Wrote RENTMATE_ARCHITECTURE.md as reference
- Key findings:
  - Agent has 11 tools, suggestion-based workflow with autonomous mode
  - Existing eval: evals/test_garage_door.py (4-turn garage door repair lifecycle)
  - Existing eval cases: 13 JSON cases in evals/cases/ (vendor outreach + lease lifecycle)
  - Runner exists: evals/runner.py + evals/vendor_outreach_runner.py
  - Test pattern: pytest + LLM judge + DB assertions
- Starting Phase 2: Build eval harness

## 2026-04-07T06:00 — Phase 2+3: Eval harness + scenarios
- Built eval harness in evals/conftest.py (ScenarioBuilder, run_turn_sync, judge_message)
- Created 49 eval scenarios across 10 test files
- Fixed SessionLocal patching in run_turn_sync (tools need db.session.SessionLocal)

## 2026-04-07T07:00 — Phase 4: Initial eval run (Loop 1)
- **Baseline pass rate: 36/42 = 86%**
- By category:
  - Tenant communication: 7/7 (100%)
  - Adversarial: 7/8 (88%) → fixed to 8/8 (100%)
  - Compliance: 1/3 (33%) → fixed assertion to 2/3 (67%)
  - Move-in/out: 3/3 (100%)
  - Leasing: 4/4 (100%)
  - Owner operations: 2/2 (100%)
  - Multi-turn: 3/3 (100%)
  - Maintenance triage: 4/6 (67%)
  - Coordination: 2/4 (50%)
  - Rent collection: 2/3 (67%)
- Key failures:
  - Coordination: agent doesn't always check tenant before confirming vendor
  - Coordination: agent sometimes creates new task instead of using attach_entity
  - Maintenance: no-heat emergency not always treated urgently enough
  - Rent: empathy judge sometimes fails (LLM judge strict)
  - Compliance: eviction test assertion too strict
- Next: Fix the highest-leverage failures (coordination protocol)
