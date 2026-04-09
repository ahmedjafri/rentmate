#!/usr/bin/env python3
"""
Seed test data into the RentMate database.

Usage:
    poetry run python scripts/seed_data.py

Creates 3 Seattle-area properties, units, 8 tenants, and leases
under the first Account found in the database.
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from db.models import Account, Lease, Property, Tenant, Unit
from db.session import SessionLocal

db = SessionLocal.session_factory()

# ------------------------------------------------------------------
# Resolve account (creator_id)
# ------------------------------------------------------------------
account = db.execute(select(Account).order_by(Account.created_at)).scalars().first()
if not account:
    print("ERROR: No Account found. Start the server first to create the default account.")
    sys.exit(1)

creator_id = account.id
print(f"Seeding under creator_id={creator_id}")

# ------------------------------------------------------------------
# Properties
# ------------------------------------------------------------------
props_data = [
    {
        "name": "The Meadows",
        "address_line1": "1842 Meadow Lane",
        "city": "Bellevue", "state": "WA", "postal_code": "98004",
        "units": ["Unit 1A", "Unit 1B", "Unit 2A", "Unit 2B"],
    },
    {
        "name": "Pinecrest Apartments",
        "address_line1": "3310 Pine Street",
        "city": "Redmond", "state": "WA", "postal_code": "98052",
        "units": ["Unit 101", "Unit 102", "Unit 201", "Unit 202", "Unit 301"],
    },
    {
        "name": "Harbor View",
        "address_line1": "520 Harbor Blvd",
        "address_line2": "Suite 100",
        "city": "Kirkland", "state": "WA", "postal_code": "98033",
        "units": ["Studio A", "Studio B", "1BR North", "1BR South"],
    },
]

created_props = []
for pd in props_data:
    units_labels = pd.pop("units")
    p = Property(creator_id=creator_id, country="USA", **pd)
    db.add(p)
    db.flush()
    units = []
    for label in units_labels:
        u = Unit(creator_id=creator_id, property_id=p.id, label=label)
        db.add(u)
        units.append(u)
    db.flush()
    created_props.append((p, units))
    print(f"  Created property: {p.name} with {len(units)} units")

# ------------------------------------------------------------------
# Tenants
# ------------------------------------------------------------------
tenants_data = [
    {"first_name": "Marcus", "last_name": "Johnson", "email": "marcus.johnson@gmail.com", "phone": "+14255550101"},
    {"first_name": "Priya",  "last_name": "Patel",   "email": "priya.patel@gmail.com",   "phone": "+14255550102"},
    {"first_name": "Devon",  "last_name": "Torres",  "email": "devon.torres@gmail.com",  "phone": "+14255550103"},
    {"first_name": "Aisha",  "last_name": "Williams","email": "aisha.w@outlook.com",      "phone": "+14255550104"},
    {"first_name": "Ryan",   "last_name": "Chen",    "email": "ryan.chen@gmail.com",      "phone": "+14255550105"},
    {"first_name": "Sofia",  "last_name": "Martinez","email": "sofia.m@yahoo.com",        "phone": "+14255550106"},
    {"first_name": "Tyler",  "last_name": "Brooks",  "email": "tbrooks@gmail.com",        "phone": "+14255550107"},
    {"first_name": "Nadia",  "last_name": "Kim",     "email": "nadia.kim@gmail.com",      "phone": "+14255550108"},
]

tenants = []
for td in tenants_data:
    t = Tenant(creator_id=creator_id, **td)
    db.add(t)
    tenants.append(t)
db.flush()
print(f"  Created {len(tenants)} tenants")

# ------------------------------------------------------------------
# Leases  (8 tenants spread across units, a few units vacant)
# ------------------------------------------------------------------
# prop0=The Meadows (4 units), prop1=Pinecrest (5 units), prop2=Harbor View (4 units)
# Assign 8 tenants to 8 of the 13 units, leaving 5 vacant
lease_assignments = [
    # (prop_idx, unit_idx, tenant_idx, start, end, rent)
    (0, 0, 0, date(2024, 2, 1),  date(2025, 1, 31),  1850.0),   # Meadows 1A — Marcus
    (0, 1, 1, date(2024, 4, 1),  date(2025, 3, 31),  1850.0),   # Meadows 1B — Priya
    (0, 2, 2, date(2023, 9, 1),  date(2025, 8, 31),  1950.0),   # Meadows 2A — Devon
    (1, 0, 3, date(2024, 1, 1),  date(2024, 12, 31), 1600.0),   # Pinecrest 101 — Aisha (expired)
    (1, 1, 4, date(2024, 6, 1),  date(2025, 5, 31),  1650.0),   # Pinecrest 102 — Ryan
    (1, 2, 5, date(2024, 3, 1),  date(2026, 2, 28),  1700.0),   # Pinecrest 201 — Sofia
    (2, 0, 6, date(2024, 8, 1),  date(2025, 7, 31),  1400.0),   # Harbor Studio A — Tyler
    (2, 2, 7, date(2024, 5, 1),  date(2025, 4, 30),  1750.0),   # Harbor 1BR North — Nadia
]

for pi, ui, ti, start, end, rent in lease_assignments:
    prop, units = created_props[pi]
    unit = units[ui]
    tenant = tenants[ti]
    lease = Lease(
        creator_id=creator_id,
        tenant_id=tenant.id,
        unit_id=unit.id,
        property_id=prop.id,
        start_date=start,
        end_date=end,
        rent_amount=rent,
    )
    db.add(lease)

db.flush()
print(f"  Created {len(lease_assignments)} leases")

db.commit()
print("\nDone! Test data seeded successfully.")
db.close()
