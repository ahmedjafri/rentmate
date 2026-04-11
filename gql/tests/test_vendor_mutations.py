from gql.schema import schema
from db.models import User


def _context(db):
    return {
        "db_session": db,
        "user": {
            "uid": "test-user-1",
            "email": "test-admin@example.com",
            "username": "test-admin@example.com",
        },
    }


def test_create_vendor_returns_external_uid_and_persists_current_schema(db):
    result = schema.execute_sync(
        """
        mutation {
          createVendor(
            input: {
              name: "Bob Plumber"
              phone: "+15550001111"
              vendorType: "Plumber"
              email: "bob@example.com"
            }
          ) {
            uid
            name
            vendorType
            email
          }
        }
        """,
        context_value=_context(db),
    )

    assert result.errors is None
    vendor_uid = result.data["createVendor"]["uid"]
    vendor = db.query(User).filter_by(external_id=vendor_uid, user_type="vendor").one()

    assert result.data["createVendor"] == {
        "uid": vendor.external_id,
        "name": "Bob Plumber",
        "vendorType": "Plumber",
        "email": "bob@example.com",
    }
    assert vendor.org_id == 1
    assert vendor.creator_id == 1


def test_update_and_delete_vendor_use_external_uid(db):
    vendor = User(
        org_id=1,
        creator_id=1,
        user_type="vendor",
        first_name="Old",
        last_name="Vendor",
        phone="+15550002222",
        role_label="Plumber",
    )
    db.add(vendor)
    db.flush()

    update_result = schema.execute_sync(
        f"""
        mutation {{
          updateVendor(
            input: {{
              uid: "{vendor.external_id}"
              name: "New Vendor"
              vendorType: "Electrician"
            }}
          ) {{
            uid
            name
            vendorType
          }}
        }}
        """,
        context_value=_context(db),
    )

    assert update_result.errors is None
    assert update_result.data["updateVendor"] == {
        "uid": vendor.external_id,
        "name": "New Vendor",
        "vendorType": "Electrician",
    }

    delete_result = schema.execute_sync(
        f"""
        mutation {{
          deleteVendor(uid: "{vendor.external_id}")
        }}
        """,
        context_value=_context(db),
    )

    assert delete_result.errors is None
    assert delete_result.data == {"deleteVendor": True}
    assert db.query(User).filter_by(external_id=vendor.external_id, user_type="vendor").one_or_none() is None
