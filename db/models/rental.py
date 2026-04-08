import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from .base import Base, HasAccountId, HasContext


class Property(Base, HasAccountId, HasContext):
    """
    A property managed by the landlord.
    """

    __tablename__ = "properties"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    name = Column(String(255), nullable=True)
    address_line1 = Column(String(255), nullable=False)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    country = Column(String(100), nullable=True, default="USA")
    # 'single_family' — one tenant, no distinct units (house/condo)
    # 'multi_family'  — multiple units (apartment building, duplex, etc.)
    property_type = Column(String(20), nullable=True, default='multi_family')
    source = Column(String(20), nullable=True)  # 'manual' | 'document'

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    units = relationship(
        "Unit",
        back_populates="property",
        cascade="all, delete-orphan",
    )

    leases = relationship(
        "Lease",
        back_populates="property",
        cascade="all, delete-orphan",
    )


class Unit(Base, HasAccountId, HasContext):
    """
    A rentable unit within a property.
    """

    __tablename__ = "units"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    property_id = Column(
        String(36),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )

    label = Column(String(100), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "property_id",
            "label",
            name="uq_units_property_label",
        ),
    )

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    property = relationship("Property", back_populates="units")

    leases = relationship(
        "Lease",
        back_populates="unit",
        cascade="all, delete-orphan",
    )


class Tenant(Base, HasAccountId, HasContext):
    """
    A tenant/contact.
    """

    __tablename__ = "tenants"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    notes = Column(Text, nullable=True)

    extra = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    leases = relationship(
        "Lease",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    @property
    def units(self):
        return [lease.unit for lease in self.leases if lease.unit is not None]


class Lease(Base, HasAccountId):
    """
    A lease agreement between a tenant and a unit.
    """

    __tablename__ = "leases"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))

    tenant_id = Column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )

    unit_id = Column(
        String(36),
        ForeignKey("units.id", ondelete="CASCADE"),
        nullable=False,
    )

    property_id = Column(
        String(36),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
    )

    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    rent_amount = Column(Float, nullable=False)
    payment_status = Column(String(20), nullable=True, default='current')  # current/late/overdue

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    tenant = relationship("Tenant", back_populates="leases")
    unit = relationship("Unit", back_populates="leases")
    property = relationship("Property", back_populates="leases")
