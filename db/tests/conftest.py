import pytest

from db.models import User


@pytest.fixture(autouse=True)
def _set_creator_context():
    """Override the root test fixture with the current auth signature."""
    from integrations.local_auth import reset_request_context, set_request_context

    tokens = set_request_context(account_id=1, org_id=1)
    try:
        yield
    finally:
        reset_request_context(tokens)


@pytest.fixture(autouse=True)
def current_user(db):
    """Seed the account user required by creator foreign keys."""
    user = db.get(User, 1)
    if user is None:
        user = User(
            id=1,
            org_id=1,
            email="db-tests@example.com",
            active=True,
        )
        db.add(user)
        db.flush()
    return user
