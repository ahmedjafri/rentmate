from llm.rentmate_policy_provider import RentmatePolicyProvider, select_policy_keys


def test_select_policy_keys_for_eviction_query():
    tags = select_policy_keys("The 14 days passed and the tenant still has not paid. Coordinate with our lawyer on filing.")
    assert "legal_compliance" in tags
    assert "coordination" in tags


def test_select_policy_keys_for_hostile_tenant_query():
    tags = select_policy_keys("The tenant is angry and says this repair delay is unacceptable.")
    assert "communication" in tags


def test_prefetch_returns_policy_text(tmp_path):
    provider = RentmatePolicyProvider()
    provider.initialize(session_id="abc", hermes_home=str(tmp_path))
    text = provider.prefetch("Coordinate with the vendor and tenant about the appointment time.")
    assert "Coordination Policy" in text
    assert "Confirm access with the tenant before confirming the schedule with the vendor." in text
