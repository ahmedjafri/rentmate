import uuid
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from db.models import Property as SqlProperty, Unit as SqlUnit


class PropertyService:
    @staticmethod
    def create_property(
        sess: Session,
        address: str,
        property_type: str = "multi_family",
        name: str | None = None,
        city: str | None = None,
        state: str | None = None,
        postal_code: str | None = None,
        unit_labels: list[str] | None = None,
    ) -> tuple[SqlProperty, list[SqlUnit]]:
        prop = SqlProperty(
            id=str(uuid.uuid4()),
            name=name,
            address_line1=address,
            city=city,
            state=state,
            postal_code=postal_code,
            property_type=property_type,
            source="manual",
            created_at=datetime.utcnow(),
        )
        sess.add(prop)
        sess.flush()

        units: list[SqlUnit] = []
        if property_type == "single_family":
            unit = SqlUnit(id=str(uuid.uuid4()), property_id=prop.id, label="Main", created_at=datetime.utcnow())
            sess.add(unit)
            sess.flush()
            units.append(unit)
        elif unit_labels:
            for label in unit_labels:
                label = label.strip()
                if not label:
                    continue
                unit = SqlUnit(id=str(uuid.uuid4()), property_id=prop.id, label=label, created_at=datetime.utcnow())
                sess.add(unit)
                sess.flush()
                units.append(unit)

        sess.commit()
        return prop, units

    @staticmethod
    def delete_property(sess: Session, uid: str) -> bool:
        prop = sess.execute(select(SqlProperty).where(SqlProperty.id == uid)).scalar_one_or_none()
        if not prop:
            raise ValueError(f"Property {uid} not found")
        sess.delete(prop)
        sess.commit()
        return True
