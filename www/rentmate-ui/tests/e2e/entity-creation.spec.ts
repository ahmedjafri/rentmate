/**
 * End-to-end coverage for the manual "Add property / Add unit / Add tenant"
 * flow. Drives the GraphQL mutations the UI calls (the same Document
 * exports the React pages import) so a regression like the
 * ``TenantType.from_new() takes 2 positional arguments but 4 were given``
 * crash in ``createTenantWithLease`` would fail this spec.
 *
 * Run: npx playwright test --config playwright-e2e.config.ts \
 *   tests/e2e/entity-creation.spec.ts
 */
import { test, expect, Page } from '@playwright/test';

import {
  AddLeaseForTenantDocument,
  CreatePropertyDocument,
  CreateTenantWithLeaseDocument,
  HousesDocument,
  TenantsDocument,
} from '@/graphql/generated';
import { graphqlRequest, loginViaGraphql } from './graphql';

let cachedToken: string | null = null;

async function getToken(page: Page): Promise<string> {
  if (cachedToken) return cachedToken;
  cachedToken = await loginViaGraphql(page);
  if (!cachedToken) throw new Error('Login failed');
  return cachedToken;
}

// Tagging fixtures with a per-run nonce so reruns against the same dev
// DB don't collide on uniqueness constraints (address strings, etc.).
function unique(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
}

test.describe('Entity creation: properties, units, tenants', () => {
  test('createProperty: returns a populated HouseType with unit list', async ({ page }) => {
    const token = await getToken(page);
    const address = unique('E2E Add Property');
    const result = await graphqlRequest(
      page,
      CreatePropertyDocument,
      {
        input: {
          address,
          propertyType: 'multi_family',
          name: 'Maple Court',
          unitLabels: ['1A', '1B'],
        },
      },
      token,
    );
    expect(result.createProperty.uid).toBeTruthy();
    expect(result.createProperty.address).toContain('E2E Add Property');
    // Unit list mirrors the labels we passed in.
    const unitLabels = (result.createProperty.unitList ?? []).map(u => u.label).sort();
    expect(unitLabels).toEqual(['1A', '1B']);
    // No occupants yet.
    expect(result.createProperty.occupiedUnits).toBe(0);
  });

  test('createTenantWithLease: creates the tenant + lease and returns a populated TenantType', async ({ page }) => {
    const token = await getToken(page);

    // Property + unit must exist before we can attach a tenant — drive
    // the same path the UI uses.
    const prop = await graphqlRequest(
      page,
      CreatePropertyDocument,
      {
        input: {
          address: unique('E2E Tenant Property'),
          propertyType: 'multi_family',
          unitLabels: ['2B'],
        },
      },
      token,
    );
    const propertyUid = prop.createProperty.uid;
    const unitUid = prop.createProperty.unitList?.[0]?.uid;
    expect(propertyUid).toBeTruthy();
    expect(unitUid).toBeTruthy();

    const tenantResult = await graphqlRequest(
      page,
      CreateTenantWithLeaseDocument,
      {
        input: {
          firstName: 'Marcus',
          lastName: unique('E2E'),
          propertyId: propertyUid,
          unitId: unitUid!,
          leaseStart: '2026-01-01',
          leaseEnd: '2026-12-31',
          rentAmount: 1900,
          email: `${unique('marcus')}@example.com`,
          phone: '+15550009999',
        },
      },
      token,
    );

    // Resolver must return a populated TenantType — this asserts both
    // that the mutation succeeded *and* that TenantType.from_new
    // accepted the (tenant, unit, lease) tuple unpack from the
    // resolver. The original bug surfaced here.
    expect(tenantResult.createTenantWithLease.uid).toBeTruthy();
    expect(tenantResult.createTenantWithLease.name).toContain('Marcus');
    expect(tenantResult.createTenantWithLease.unitLabel).toBe('2B');
    expect(tenantResult.createTenantWithLease.rentAmount).toBe(1900);
    expect(tenantResult.createTenantWithLease.isActive).toBe(true);

    // Tenant shows up in the listing query the UI uses.
    const list = await graphqlRequest(page, TenantsDocument, {}, token);
    const found = list.tenants.find(t => t.uid === tenantResult.createTenantWithLease.uid);
    expect(found).toBeTruthy();
    expect(found?.name).toBe(tenantResult.createTenantWithLease.name);
  });

  test('addLeaseForTenant: extends an existing tenant with a follow-on lease', async ({ page }) => {
    const token = await getToken(page);

    const prop = await graphqlRequest(
      page,
      CreatePropertyDocument,
      {
        input: {
          address: unique('E2E Renewal Property'),
          propertyType: 'multi_family',
          unitLabels: ['3C'],
        },
      },
      token,
    );
    const propertyUid = prop.createProperty.uid;
    const unitUid = prop.createProperty.unitList?.[0]?.uid;

    const tenant = await graphqlRequest(
      page,
      CreateTenantWithLeaseDocument,
      {
        input: {
          firstName: 'Priya',
          lastName: unique('E2E'),
          propertyId: propertyUid,
          unitId: unitUid!,
          leaseStart: '2026-01-01',
          leaseEnd: '2026-06-30',
          rentAmount: 2100,
        },
      },
      token,
    );
    const tenantUid = tenant.createTenantWithLease.uid;

    // Same code path on the resolver, same risk surface for the
    // signature mismatch — assert the renewal returns a tenant payload
    // populated from the *new* lease.
    const renewed = await graphqlRequest(
      page,
      AddLeaseForTenantDocument,
      {
        input: {
          tenantId: tenantUid,
          propertyId: propertyUid,
          unitId: unitUid!,
          leaseStart: '2026-07-01',
          leaseEnd: '2027-06-30',
          rentAmount: 2200,
        },
      },
      token,
    );
    expect(renewed.addLeaseForTenant.uid).toBe(tenantUid);
    expect(renewed.addLeaseForTenant.unitLabel).toBe('3C');
    expect(renewed.addLeaseForTenant.leaseEndDate).toBe('2027-06-30');
    expect(renewed.addLeaseForTenant.rentAmount).toBe(2200);
  });

  test('properties listing reflects newly created units', async ({ page }) => {
    const token = await getToken(page);
    const created = await graphqlRequest(
      page,
      CreatePropertyDocument,
      {
        input: {
          address: unique('E2E List Property'),
          propertyType: 'single_family',
          unitLabels: ['Main'],
        },
      },
      token,
    );

    const list = await graphqlRequest(page, HousesDocument, {}, token);
    const match = list.houses.find(h => h.uid === created.createProperty.uid);
    expect(match).toBeTruthy();
    expect(match?.unitList?.[0]?.label).toBe('Main');
  });
});
