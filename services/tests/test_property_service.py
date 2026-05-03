from gql.types import UpdatePropertyInput
from services.property_service import PropertyService


def test_create_property_creates_units_and_scopes_to_current_context(db):
    prop, units = PropertyService.create_property(
        db,
        address="123 Main St",
        property_type="multi_family",
        name="Maple Apartments",
        city="Boston",
        state="MA",
        postal_code="02108",
        unit_labels=["1A", " ", "1B"],
    )

    assert prop.org_id == 1
    assert prop.creator_id == 1
    assert prop.name == "Maple Apartments"
    assert [unit.label for unit in units] == ["1A", "1B"]
    assert all(unit.org_id == 1 for unit in units)
    assert all(unit.creator_id == 1 for unit in units)


def test_create_single_family_property_adds_main_unit(db):
    prop, units = PropertyService.create_property(
        db,
        address="9 Oak Ave",
        property_type="single_family",
    )

    assert prop.property_type == "single_family"
    assert len(units) == 1
    assert units[0].label == "Main"


def test_update_and_delete_property_scope_by_current_user(db):
    prop, _ = PropertyService.create_property(db, address="77 Pine Rd")

    updated = PropertyService.update_property(
        db,
        UpdatePropertyInput(uid=prop.id, name="Updated", address="88 Cedar Rd", property_type="single_family"),
    )

    assert updated.name == "Updated"
    assert updated.address_line1 == "88 Cedar Rd"
    assert updated.property_type == "single_family"

    assert PropertyService.delete_property(db, prop.id) is True
    assert db.get(type(prop), prop.id) is None
