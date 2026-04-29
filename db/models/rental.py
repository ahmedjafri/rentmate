from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import relationship

from .base import Base, HasContext, HasCreatorId, OrgId, PrimaryId, SmallPrimaryId


class Property(Base, OrgId, PrimaryId, HasCreatorId, HasContext):
    """A property managed by the landlord."""

    __tablename__ = "properties"

    name = Column(String(255), nullable=True)
    address_line1 = Column(String(255), nullable=False)
    address_line2 = Column(String(255), nullable=True)
    city = Column(String(100), nullable=True)
    state = Column(String(100), nullable=True)
    postal_code = Column(String(20), nullable=True)
    country = Column(String(100), nullable=True, default="USA")
    # 'single_family' -- one tenant, no distinct units (house/condo)
    # 'multi_family'  -- multiple units (apartment building, duplex, etc.)
    property_type = Column(String(20), nullable=True, default="multi_family")
    source = Column(String(20), nullable=True)  # 'manual' | 'document'

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    units = relationship("Unit", back_populates="property", cascade="all, delete-orphan")
    leases = relationship(
        "Lease",
        back_populates="property",
        cascade="all, delete-orphan",
        overlaps="unit,tenant,leases",
    )

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_properties_server"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
    )


class Unit(Base, OrgId, PrimaryId, HasCreatorId, HasContext):
    """A rentable unit within a property."""

    __tablename__ = "units"

    property_id = Column(String(36), nullable=False)
    label = Column(String(100), nullable=False)
    tenant_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    property = relationship("Property", back_populates="units")
    tenant = relationship("Tenant", foreign_keys=[tenant_id])
    leases = relationship(
        "Lease",
        back_populates="unit",
        cascade="all, delete-orphan",
        overlaps="property,tenant,leases",
    )

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_units_server"),
        UniqueConstraint("property_id", "label", name="uq_units_property_label"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "property_id"],
            ["properties.org_id", "properties.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["tenants.org_id", "tenants.id"],
            ondelete="SET NULL",
        ),
    )


class LeaseTenant(Base, OrgId):
    """Association between a lease and each tenant on that lease."""

    __tablename__ = "lease_tenants"

    lease_id = Column(String(36), nullable=False)
    tenant_id = Column(Integer, nullable=False)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    lease = relationship("Lease", back_populates="tenant_links", overlaps="lease_links,tenant")
    tenant = relationship("Tenant", back_populates="lease_links", overlaps="lease,tenant_links")

    __table_args__ = (
        PrimaryKeyConstraint("org_id", "lease_id", "tenant_id", name="pk_lease_tenants"),
        ForeignKeyConstraint(
            ["org_id", "lease_id"],
            ["leases.org_id", "leases.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["tenants.org_id", "tenants.id"],
            ondelete="CASCADE",
        ),
    )


class Tenant(Base, OrgId, SmallPrimaryId, HasCreatorId, HasContext):
    """A tenant/contact."""

    __tablename__ = "tenants"

    user_id = Column(Integer, nullable=False, index=True)
    extra = Column(JSON, nullable=True)

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    user = relationship("User", foreign_keys=[user_id])
    lease_links = relationship(
        "LeaseTenant",
        back_populates="tenant",
        cascade="all, delete-orphan",
        overlaps="lease,tenant_links",
    )
    leases = relationship(
        "Lease",
        secondary="lease_tenants",
        back_populates="tenants",
        viewonly=True,
        overlaps="property,unit,leases",
    )

    @property
    def units(self):
        return [lease.unit for lease in self.leases if lease.unit is not None]

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_tenants_server"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "user_id"],
            ["users.org_id", "users.id"],
            ondelete="CASCADE",
        ),
    )


class Lease(Base, OrgId, PrimaryId, HasCreatorId):
    """A lease agreement between a tenant and a unit."""

    __tablename__ = "leases"

    tenant_id = Column(Integer, nullable=False)
    unit_id = Column(String(36), nullable=False)
    property_id = Column(String(36), nullable=False)

    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    rent_amount = Column(Float, nullable=False)
    payment_status = Column(String(20), nullable=True, default="current")  # current/late/overdue

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))

    tenant_links = relationship(
        "LeaseTenant",
        back_populates="lease",
        cascade="all, delete-orphan",
        overlaps="lease_links,tenant",
    )
    tenants = relationship(
        "Tenant",
        secondary="lease_tenants",
        back_populates="leases",
        viewonly=True,
        overlaps="property,unit,leases",
    )
    tenant = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
        overlaps="property,unit,leases",
    )
    unit = relationship(
        "Unit",
        back_populates="leases",
        foreign_keys=[unit_id],
        overlaps="property,tenant,leases",
    )
    property = relationship(
        "Property",
        back_populates="leases",
        foreign_keys=[property_id],
        overlaps="unit,tenant,leases",
    )

    __table_args__ = (
        UniqueConstraint("org_id", "id", name="uq_leases_server"),
        ForeignKeyConstraint(
            ["org_id", "creator_id"],
            ["users.org_id", "users.id"],
        ),
        ForeignKeyConstraint(
            ["org_id", "tenant_id"],
            ["tenants.org_id", "tenants.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "unit_id"],
            ["units.org_id", "units.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["org_id", "property_id"],
            ["properties.org_id", "properties.id"],
            ondelete="CASCADE",
        ),
    )


@event.listens_for(Lease, "after_insert")
def _insert_primary_lease_tenant(_mapper, connection, target: Lease) -> None:
    """Keep legacy ``Lease(tenant_id=...)`` writes visible through ``Lease.tenants``."""
    if target.tenant_id is None:
        return
    connection.execute(
        LeaseTenant.__table__.insert().values(
            org_id=target.org_id,
            lease_id=target.id,
            tenant_id=target.tenant_id,
            created_at=target.created_at,
        )
    )
