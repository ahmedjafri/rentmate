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
    assert "the next action is to contact the tenant for access confirmation" in text
    assert "Do not call `close_task` while a coordination handshake is still incomplete." in text


def test_select_policy_keys_for_document_upload_query():
    tags = select_policy_keys("I uploaded a lease PDF and the tenant name is missing. Create the property and ask for the tenant full name.")
    assert "document_handling" in tags
    assert "communication" in tags


def test_prefetch_returns_document_handling_policy(tmp_path):
    provider = RentmatePolicyProvider()
    provider.initialize(session_id="abc", hermes_home=str(tmp_path))
    text = provider.prefetch("The uploaded rental agreement is missing the tenant name. Ask for the full name before creating the tenant.")
    assert "Document Handling Policy" in text
    assert "Do not fabricate people from document context" in text


def test_select_policy_keys_for_information_gap_query():
    tags = select_policy_keys("I don't have the tenant's security deposit refund rule on file. I'll check with the property manager.")
    assert "information_gaps" in tags
    assert "communication" in tags


def test_prefetch_returns_information_gaps_policy(tmp_path):
    provider = RentmatePolicyProvider()
    provider.initialize(session_id="abc", hermes_home=str(tmp_path))
    text = provider.prefetch("I do not have the late fee amount on file and need to check with the property manager.")
    assert "Information Gaps Policy" in text
    assert "Do not call `close_task`" in text


def test_prefetch_returns_fire_911_safety_policy(tmp_path):
    provider = RentmatePolicyProvider()
    provider.initialize(session_id="abc", hermes_home=str(tmp_path))
    text = provider.prefetch("There is a fire at Meadow Lane. What is the status on 911?")
    assert "Safety Policy" in text
    assert "RentMate cannot call 911" in text
    assert "Escalate active fires" in text
