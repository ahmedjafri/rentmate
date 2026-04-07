# Legacy Neo4j tests — not applicable to the OSS version.
# Kept for reference only; not collected by pytest (prefixed with _).
#
# import unittest
# import neomodel
# from testcontainers.neo4j import Neo4jContainer
import datetime

from db.house_tenant_graph import House, HouseTenantGraph, Tenant


class TestHouseTenantGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        """Starts a temporary Neo4j container for all tests in this class."""
        cls.neo4j_container = Neo4jContainer("neo4j:5.20.0", password="test_password")
        cls.neo4j_container.start()

        # Build bolt URI with test password
        uri = f"bolt://neo4j:test_password@{cls.neo4j_container.get_container_host_ip()}:{cls.neo4j_container.get_exposed_port(7687)}"
        neomodel.config.DATABASE_URL = uri

        cls.graph = HouseTenantGraph()

    @classmethod
    def tearDownClass(cls):
        cls.neo4j_container.stop()

    def setUp(self):
        # Clean up database before each test
        neomodel.db.cypher_query("MATCH (n) DETACH DELETE n")

    def test_add_house_and_tenant(self):
        house = self.graph.add_house("Test House", "123 Test Street")
        tenant = self.graph.add_tenant("Alice")

        self.assertIsInstance(house, House)
        self.assertIsInstance(tenant, Tenant)

        self.assertEqual(house.name, "Test House")
        self.assertEqual(tenant.name, "Alice")

    def test_add_relationship_and_query(self):
        house = self.graph.add_house("My House", "456 Avenue")
        tenant = self.graph.add_tenant("Bob")

        self.graph.add_relationship(tenant, house)

        # Pass objects directly now
        tenants_for_house = self.graph.get_tenants_for_house(house)
        self.assertEqual(len(tenants_for_house), 1)
        self.assertEqual(tenants_for_house[0].uid, tenant.uid)

        house_for_tenant = self.graph.get_house_for_tenant(tenant)
        self.assertIsNotNone(house_for_tenant)
        self.assertEqual(house_for_tenant.uid, house.uid)

    def test_get_tenants_for_nonexistent_house(self):
        # Create a dummy House object (not in DB)
        dummy_house = House(name="Ghost House", address="Nowhere").save()
        neomodel.db.cypher_query("MATCH (n) DETACH DELETE n")  # delete it so it's nonexistent
        tenants = self.graph.get_tenants_for_house(dummy_house)
        self.assertEqual(tenants, [])

    def test_get_house_for_nonexistent_tenant(self):
        # Create a dummy Tenant object (not in DB)
        dummy_tenant = Tenant(name="Ghost Tenant").save()
        neomodel.db.cypher_query("MATCH (n) DETACH DELETE n")  # delete it so it's nonexistent
        house = self.graph.get_house_for_tenant(dummy_tenant)
        self.assertIsNone(house)

    def test_add_lease(self):
        """Test creating a lease connecting a tenant and a house."""
        house = self.graph.add_house("Lease House", "789 Road")
        tenant = self.graph.add_tenant("Charlie")

        # Create lease
        lease = Lease(
            start_date=datetime.date(2025, 9, 1),
            end_date=datetime.date(2026, 8, 31),
            rent_amount=2500.0
        ).save()

        # Connect lease to tenant and house
        tenant.leases.connect(lease)
        house.leases.connect(lease)

        # Verify connections
        self.assertIn(lease, tenant.leases.all())
        self.assertIn(lease, house.leases.all())

    def test_query_leases_for_tenant(self):
        """Test querying all leases for a given tenant."""
        house1 = self.graph.add_house("House A", "111 A St")
        house2 = self.graph.add_house("House B", "222 B St")
        tenant = self.graph.add_tenant("Dana")

        # Create two leases
        lease1 = Lease(start_date=datetime.date(2025, 1, 1), end_date=datetime.date(2025, 12, 31), rent_amount=1200).save()
        lease2 = Lease(start_date=datetime.date(2026, 1, 1), end_date=datetime.date(2026, 12, 31), rent_amount=1300).save()

        tenant.leases.connect(lease1)
        tenant.leases.connect(lease2)
        house1.leases.connect(lease1)
        house2.leases.connect(lease2)

        leases = tenant.leases.all()
        self.assertEqual(len(leases), 2)
        self.assertIn(lease1, leases)
        self.assertIn(lease2, leases)

    def test_query_leases_for_house(self):
        """Test querying all leases for a given house."""
        house = self.graph.add_house("Big House", "333 C St")
        tenant1 = self.graph.add_tenant("Eve")
        tenant2 = self.graph.add_tenant("Frank")

        lease1 = Lease(start_date=datetime.date(2025, 5, 1), end_date=datetime.date(2026, 4, 30), rent_amount=1500).save()
        lease2 = Lease(start_date=datetime.date(2026, 5, 1), end_date=datetime.date(2027, 4, 30), rent_amount=1600).save()

        tenant1.leases.connect(lease1)
        tenant2.leases.connect(lease2)
        house.leases.connect(lease1)
        house.leases.connect(lease2)

        leases = house.leases.all()
        self.assertEqual(len(leases), 2)
        self.assertIn(lease1, leases)
        self.assertIn(lease2, leases)



if __name__ == "__main__":
    unittest.main()
