#!/usr/bin/env python3
"""
Seed development dummy data into the local RentMate SQLite database.

Creates:
  - 2 existing properties with units, tenants, and active leases
    (so the Portfolio Overview and KPI cards have real data)
  - 3 processed documents whose extracted_data triggers dashboard suggestions:
      doc1 → location (new property) + tenant (new tenant)
      doc2 → tenant (new tenant at existing property)
      doc3 → tenant update + lease update (existing tenant, different email/rent)

Usage:
    poetry run python scripts/seed_dummy_data.py

Idempotent — re-running is safe (checks for existing dummy documents first).
"""
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ.setdefault("RENTMATE_DB_PATH", "./data/rentmate.db")

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

DB_PATH = os.getenv("RENTMATE_DB_PATH", "./data/rentmate.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

from db.models import Base, Property, Unit, Tenant, Lease, Document

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
Base.metadata.create_all(engine)

# Ensure new columns exist on older DBs
with engine.connect() as conn:
    for col, typ in [("sha256_checksum", "TEXT"), ("suggestion_states", "TEXT")]:
        try:
            conn.execute(text(f"ALTER TABLE documents ADD COLUMN {col} {typ}"))
            conn.commit()
        except Exception:
            pass

Session = sessionmaker(bind=engine)
db = Session()

print("Seeding dummy data…\n")
now = datetime.utcnow()


# ── Existing properties ───────────────────────────────────────────────────────
prop1 = Property(
    id=str(uuid.uuid4()), name="The Meadows",
    address_line1="1842 Meadow Lane",
    city="Bellevue", state="WA", postal_code="98004", created_at=now,
)
prop2 = Property(
    id=str(uuid.uuid4()), name="Pinecrest Apartments",
    address_line1="3310 Pine Street",
    city="Redmond", state="WA", postal_code="98052", created_at=now,
)
db.add_all([prop1, prop2])
db.flush()
print(f"  + {prop1.name}  ({prop1.address_line1})")
print(f"  + {prop2.name}  ({prop2.address_line1})")


# ── Units ─────────────────────────────────────────────────────────────────────
u1a = Unit(id=str(uuid.uuid4()), property_id=prop1.id, label="Unit 1A", created_at=now)
u1b = Unit(id=str(uuid.uuid4()), property_id=prop1.id, label="Unit 1B", created_at=now)
u1c = Unit(id=str(uuid.uuid4()), property_id=prop1.id, label="Unit 2A", created_at=now)
u2a = Unit(id=str(uuid.uuid4()), property_id=prop2.id, label="Unit 101", created_at=now)
u2b = Unit(id=str(uuid.uuid4()), property_id=prop2.id, label="Unit 102", created_at=now)
u2c = Unit(id=str(uuid.uuid4()), property_id=prop2.id, label="Unit 201", created_at=now)  # used by doc2
u2d = Unit(id=str(uuid.uuid4()), property_id=prop2.id, label="Unit 202", created_at=now)
db.add_all([u1a, u1b, u1c, u2a, u2b, u2c, u2d])
db.flush()
print(f"  + 7 units across both properties")


# ── Existing tenants ──────────────────────────────────────────────────────────
marcus  = Tenant(id=str(uuid.uuid4()), first_name="Marcus", last_name="Johnson",  email="marcus.j@gmail.com",      phone="+14255550101", created_at=now)
priya   = Tenant(id=str(uuid.uuid4()), first_name="Priya",  last_name="Patel",    email="priya.patel@gmail.com",   phone="+14255550102", created_at=now)
devon   = Tenant(id=str(uuid.uuid4()), first_name="Devon",  last_name="Torres",   email="devon.t@gmail.com",       phone="+14255550103", created_at=now)
aisha   = Tenant(id=str(uuid.uuid4()), first_name="Aisha",  last_name="Williams", email="aisha.w@outlook.com",     phone="+14255550104", created_at=now)
db.add_all([marcus, priya, devon, aisha])
db.flush()
print(f"  + 4 tenants: Marcus Johnson, Priya Patel, Devon Torres, Aisha Williams")


# ── Active leases ─────────────────────────────────────────────────────────────
leases = [
    Lease(id=str(uuid.uuid4()), tenant_id=marcus.id,  unit_id=u1a.id, property_id=prop1.id, start_date=date(2025, 2, 1), end_date=date(2026, 1, 31), rent_amount=1850.0, created_at=now),
    Lease(id=str(uuid.uuid4()), tenant_id=priya.id,   unit_id=u1b.id, property_id=prop1.id, start_date=date(2025, 4, 1), end_date=date(2026, 3, 31), rent_amount=1850.0, created_at=now),
    Lease(id=str(uuid.uuid4()), tenant_id=devon.id,   unit_id=u2a.id, property_id=prop2.id, start_date=date(2025, 6, 1), end_date=date(2026, 5, 31), rent_amount=1600.0, created_at=now),
    Lease(id=str(uuid.uuid4()), tenant_id=aisha.id,   unit_id=u2b.id, property_id=prop2.id, start_date=date(2025, 3, 1), end_date=date(2026, 2, 28), rent_amount=1650.0, created_at=now),
]
db.add_all(leases)
db.flush()
print(f"  + 4 active leases")


# ── Dummy documents that drive dashboard suggestions ──────────────────────────
print()

# Doc 1 ── Brand-new tenant at brand-new address
# Suggestions generated: location (create_property) + tenant (create_tenant)
doc1 = Document(
    id=str(uuid.uuid4()),
    filename="dummy_harbor_view_riley.pdf",
    content_type="application/pdf",
    document_type="lease",
    status="done",
    extracted_data={
        "tenant_first_name": "Riley",
        "tenant_last_name":  "Nakamura",
        "tenant_email":      "riley.nakamura@gmail.com",
        "tenant_phone":      "+12065550201",
        "property_address":  "520 Harbor Blvd",
        "unit_label":        "Suite 3A",
        "lease_start_date":  "2026-04-01",
        "lease_end_date":    "2027-03-31",
        "monthly_rent":      2100,
    },
    created_at=now,
    processed_at=now,
)
print(f"  + doc1: dummy_harbor_view_riley.pdf")
print(f"         → suggestions: location (new property) + tenant (new tenant)")

# Doc 2 ── New tenant at an existing property with an existing vacant unit
# Suggestions generated: tenant (create_tenant)
doc2 = Document(
    id=str(uuid.uuid4()),
    filename="dummy_pinecrest_lena_chen.pdf",
    content_type="application/pdf",
    document_type="lease",
    status="done",
    extracted_data={
        "tenant_first_name": "Lena",
        "tenant_last_name":  "Chen",
        "tenant_email":      "lena.chen@outlook.com",
        "tenant_phone":      "+12065550202",
        "property_address":  "3310 Pine Street",
        "unit_label":        "Unit 201",
        "lease_start_date":  "2026-05-01",
        "lease_end_date":    "2027-04-30",
        "monthly_rent":      1750,
    },
    created_at=now,
    processed_at=now,
)
print(f"  + doc2: dummy_pinecrest_lena_chen.pdf")
print(f"         → suggestions: tenant (new tenant at existing property)")

# Doc 3 ── Renewal for Marcus Johnson — different email and higher rent
# Suggestions generated: tenant (update email) + lease (update rent + end date)
doc3 = Document(
    id=str(uuid.uuid4()),
    filename="dummy_meadows_marcus_renewal.pdf",
    content_type="application/pdf",
    document_type="lease",
    status="done",
    extracted_data={
        "tenant_first_name": "Marcus",
        "tenant_last_name":  "Johnson",
        "tenant_email":      "marcus.johnson@newmail.com",   # differs from marcus.j@gmail.com
        "tenant_phone":      "+14255550101",
        "property_address":  "1842 Meadow Lane",
        "unit_label":        "Unit 1A",
        "lease_start_date":  "2026-02-01",
        "lease_end_date":    "2027-01-31",                  # extended vs current 2026-01-31
        "monthly_rent":      1975,                           # raised from 1850
    },
    created_at=now,
    processed_at=now,
)
print(f"  + doc3: dummy_meadows_marcus_renewal.pdf")
print(f"         → suggestions: tenant (update email) + lease (update rent + end date)")

db.add_all([doc1, doc2, doc3])
db.commit()

print(f"""
Done! Seeded:
  2 properties · 7 units · 4 tenants · 4 active leases
  3 processed documents → ~5 pending dashboard suggestion groups

Restart the backend then open the dashboard to see suggestions.
""")
db.close()
