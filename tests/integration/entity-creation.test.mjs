// Integration coverage for the manual "Add property / Add unit / Add tenant"
// flow. Hits the real backend over HTTP — no Playwright, no browser. Run
// against a local dev stack (``npm run dev`` then ``npm run test:integration``).
//
// What this catches:
// - The original ``TenantType.from_new() takes 2 positional arguments but
//   4 were given`` crash in ``createTenantWithLease`` would fail the
//   "manual tenant creation" test below — the resolver returns a 500 and
//   the GraphQL response surfaces it as an ``errors[]`` entry.
// - Any drift between the GraphQL schema and the input shape the React UI
//   sends (we mirror the same field names the UI uses).
//
// Prerequisites: backend reachable at ``RENTMATE_API_URL`` (default
// http://localhost:8002). Login uses the dev-mode default password.

import test from 'node:test';
import assert from 'node:assert/strict';

const API_URL = process.env.RENTMATE_API_URL ?? 'http://localhost:8002';
// Login uses the GraphQL ``login`` mutation, which creates the account
// on first sign-up if no user with that email exists. So a fresh dev DB
// works without manual setup; reruns reuse the same account.
const DEV_EMAIL = process.env.RENTMATE_DEV_EMAIL ?? 'integration-test@rentmate.local';
const DEV_PASSWORD = process.env.RENTMATE_DEV_PASSWORD ?? 'rentmate-integration';

function unique(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
}

async function gql(query, variables, token) {
  const res = await fetch(`${API_URL}/graphql`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({ query, variables }),
  });
  assert.equal(res.status, 200, `HTTP ${res.status} for ${query.slice(0, 40)}…`);
  const body = await res.json();
  if (body.errors?.length) {
    throw new Error(`GraphQL error: ${JSON.stringify(body.errors)}`);
  }
  return body.data;
}

let cachedToken = null;
async function login() {
  if (cachedToken) return cachedToken;
  const data = await gql(
    `mutation Login($input: LoginInput!) {
       login(input: $input) { token }
     }`,
    { input: { email: DEV_EMAIL, password: DEV_PASSWORD } },
  );
  cachedToken = data.login?.token;
  if (!cachedToken) throw new Error(`Login failed against ${API_URL}`);
  return cachedToken;
}

const CREATE_PROPERTY = `
  mutation CreateProperty($input: CreatePropertyInput!) {
    createProperty(input: $input) {
      uid
      address
      propertyType
      occupiedUnits
      unitList { uid label isOccupied }
    }
  }
`;

const CREATE_TENANT_WITH_LEASE = `
  mutation CreateTenantWithLease($input: CreateTenantWithLeaseInput!) {
    createTenantWithLease(input: $input) {
      uid
      name
      email
      unitLabel
      leaseEndDate
      rentAmount
      isActive
    }
  }
`;

const ADD_LEASE_FOR_TENANT = `
  mutation AddLeaseForTenant($input: AddLeaseForTenantInput!) {
    addLeaseForTenant(input: $input) {
      uid
      name
      unitLabel
      leaseEndDate
      rentAmount
    }
  }
`;

const TENANTS = `query Tenants { tenants { uid name } }`;
const HOUSES = `query Houses { houses { uid unitList { uid label } } }`;


test('createProperty returns a populated HouseType with unit list', async () => {
  const token = await login();
  const data = await gql(
    CREATE_PROPERTY,
    {
      input: {
        address: unique('Integration Property'),
        propertyType: 'multi_family',
        name: 'Maple Court',
        unitLabels: ['1A', '1B'],
      },
    },
    token,
  );
  assert.ok(data.createProperty.uid, 'createProperty should return a uid');
  assert.match(data.createProperty.address, /Integration Property/);
  const unitLabels = (data.createProperty.unitList ?? []).map(u => u.label).sort();
  assert.deepEqual(unitLabels, ['1A', '1B']);
  assert.equal(data.createProperty.occupiedUnits, 0);
});


test('createTenantWithLease creates the tenant + lease and returns a populated TenantType', async () => {
  const token = await login();

  // Property + unit must exist first — same path the UI takes.
  const prop = await gql(
    CREATE_PROPERTY,
    {
      input: {
        address: unique('Integration Tenant Property'),
        propertyType: 'multi_family',
        unitLabels: ['2B'],
      },
    },
    token,
  );
  const propertyUid = prop.createProperty.uid;
  const unitUid = prop.createProperty.unitList?.[0]?.uid;
  assert.ok(propertyUid && unitUid);

  // The original bug surfaced exactly here — resolver attempted to
  // unpack the (tenant, unit, lease) tuple into a keyword-only signature
  // and crashed with "takes 2 positional arguments but 4 were given".
  const data = await gql(
    CREATE_TENANT_WITH_LEASE,
    {
      input: {
        firstName: 'Marcus',
        lastName: unique('Integration'),
        propertyId: propertyUid,
        unitId: unitUid,
        leaseStart: '2026-01-01',
        leaseEnd: '2026-12-31',
        rentAmount: 1900,
        email: `${unique('marcus')}@example.com`,
        phone: '+15550009999',
      },
    },
    token,
  );
  assert.ok(data.createTenantWithLease.uid);
  assert.match(data.createTenantWithLease.name, /Marcus/);
  assert.equal(data.createTenantWithLease.unitLabel, '2B');
  assert.equal(data.createTenantWithLease.rentAmount, 1900);
  assert.equal(data.createTenantWithLease.isActive, true);

  // Tenant shows up in the listing query the UI uses.
  const list = await gql(TENANTS, {}, token);
  const found = list.tenants.find(t => t.uid === data.createTenantWithLease.uid);
  assert.ok(found, 'newly created tenant should appear in tenants query');
});


test('addLeaseForTenant extends an existing tenant with a follow-on lease', async () => {
  const token = await login();

  const prop = await gql(
    CREATE_PROPERTY,
    {
      input: {
        address: unique('Integration Renewal Property'),
        propertyType: 'multi_family',
        unitLabels: ['3C'],
      },
    },
    token,
  );
  const propertyUid = prop.createProperty.uid;
  const unitUid = prop.createProperty.unitList?.[0]?.uid;

  const tenant = await gql(
    CREATE_TENANT_WITH_LEASE,
    {
      input: {
        firstName: 'Priya',
        lastName: unique('Integration'),
        propertyId: propertyUid,
        unitId: unitUid,
        leaseStart: '2026-01-01',
        leaseEnd: '2026-06-30',
        rentAmount: 2100,
      },
    },
    token,
  );
  const tenantUid = tenant.createTenantWithLease.uid;

  // Same resolver shape as createTenantWithLease — same risk surface
  // for the signature mismatch — assert the renewal returns a tenant
  // payload populated from the *new* lease.
  const renewed = await gql(
    ADD_LEASE_FOR_TENANT,
    {
      input: {
        tenantId: tenantUid,
        propertyId: propertyUid,
        unitId: unitUid,
        leaseStart: '2026-07-01',
        leaseEnd: '2027-06-30',
        rentAmount: 2200,
      },
    },
    token,
  );
  assert.equal(renewed.addLeaseForTenant.uid, tenantUid);
  assert.equal(renewed.addLeaseForTenant.unitLabel, '3C');
  assert.equal(renewed.addLeaseForTenant.leaseEndDate, '2027-06-30');
  assert.equal(renewed.addLeaseForTenant.rentAmount, 2200);
});


test('properties listing reflects newly created units', async () => {
  const token = await login();
  const created = await gql(
    CREATE_PROPERTY,
    {
      input: {
        address: unique('Integration List Property'),
        propertyType: 'single_family',
        unitLabels: ['Main'],
      },
    },
    token,
  );

  const list = await gql(HOUSES, {}, token);
  const match = list.houses.find(h => h.uid === created.createProperty.uid);
  assert.ok(match, 'newly created property should appear in houses query');
  assert.equal(match.unitList?.[0]?.label, 'Main');
});
