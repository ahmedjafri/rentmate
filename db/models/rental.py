import builtins
import uuid
from datetime import datetime, date

from sqlalchemy import (
    Column,
    String,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Table,
    Text,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.orm import relationship

from .base import Base


# Many-to-many association table: a lease can have multiple tenants (roommates)
lease_tenants = Table(
    "lease_tenants",
    Base.metadata,
    Column("lease_id", String(36), ForeignKey("leases.id", ondelete="CASCADE"), primary_key=True),
    Column("tenant_id", String(36), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True),
)


class Property(Base):
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


class Unit(Base):
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


class Tenant(Base):
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

    # Legacy: leases where this tenant is the primary (via FK).
    leases = relationship(
        "Lease",
        back_populates="tenant",
        cascade="all, delete-orphan",
    )

    # All leases this tenant is associated with (via many-to-many join table).
    # This is the canonical relationship for multi-tenant support.
    shared_leases = relationship(
        "Lease",
        secondary=lease_tenants,
        back_populates="tenants",
    )

    @property
    def all_leases(self):
        """Return deduplicated list of all leases (primary + shared)."""
        seen = set()
        result = []
        for lease in self.leases:
            if lease.id not in seen:
                seen.add(lease.id)
                result.append(lease)
        for lease in self.shared_leases:
            if lease.id not in seen:
                seen.add(lease.id)
                result.append(lease)
        return result

    @property
    def units(self):
        return [lease.unit for lease in self.all_leases if lease.unit is not None]


class Lease(Base):
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

    # Many-to-many: all tenants on this lease (roommates).
    # The primary tenant (via tenant_id FK) is always included in this list
    # by the service layer when creating/adding tenants.
    tenants = relationship(
        "Tenant",
        secondary=lease_tenants,
        back_populates="shared_leases",
    )

    @builtins.property
    def all_tenants(self):
        """Return deduplicated list of all tenants (primary + co-tenants from join table)."""
        seen: set[str] = set()
        result: list = []
        if self.tenant and self.tenant.id not in seen:
            seen.add(self.tenant.id)
            result.append(self.tenant)
        for t in self.tenants:
            if t.id not in seen:
                seen.add(t.id)
                result.append(t)
        return result
