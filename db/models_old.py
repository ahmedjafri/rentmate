from neomodel import (
    config,
    StructuredNode,
    StringProperty,
    DateProperty,
    FloatProperty,
    RelationshipTo,
    RelationshipFrom,
    UniqueIdProperty,
)

from neomodel.contrib import SemiStructuredNode

import bcrypt 

class User(StructuredNode):
    """Represents a base user for authentication purposes."""
    uid = UniqueIdProperty()
    username = StringProperty(unique_index=True, required=True)
    password_hash = StringProperty(required=True)
    
    def set_password(self, password: str):
        """Hashes the password using bcrypt and stores it."""
        salt = bcrypt.gensalt()
        self.password_hash = bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')
    
    def check_password(self, password: str) -> bool:
        """Verifies a password against the stored hash."""
        return bcrypt.checkpw(password.encode('utf-8'), self.password_hash.encode('utf-8'))

class House(StructuredNode):
    """Represents a house node in the graph."""
    uid = UniqueIdProperty()
    address = StringProperty() # address need not be unique in the database. We'll check in app logic if user has an exisiting house with the same address
    name = StringProperty() # ditto above. apply uniqueness in app logic
    tenants = RelationshipFrom("Tenant", "RENTS") 
    leases = RelationshipTo("Lease", "HAS_LEASE")

class Tenant(SemiStructuredNode):
    """Represents a tenant node in the graph."""
    uid = UniqueIdProperty()
    name = StringProperty(required=True)
    email = StringProperty(required=True)
    phone = StringProperty(required=True)
    rents = RelationshipTo("House", "RENTS")
    leases = RelationshipTo("Lease", "HAS_LEASE") 

class Lease(StructuredNode):
    """Represents a lease agreement between a tenant and a house."""
    uid = UniqueIdProperty()
    start_date = DateProperty(required=True)
    end_date = DateProperty(required=True)
    rent_amount = FloatProperty(required=True)

    # Relationships to connect tenant and house
    tenant = RelationshipFrom("Tenant", "HAS_LEASE")
    house = RelationshipFrom("House", "HAS_LEASE")