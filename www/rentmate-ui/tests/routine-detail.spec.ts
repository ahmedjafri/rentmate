/**
 * E2E regression: navigating to /routines/:id must send the id as an Int to
 * the GraphQL Routine query, not as a string. If it's a string the server
 * silently returns null and the page shows "Routine not found".
 *
 * All backend endpoints are mocked — no live server required.
 */

import { test, expect, Page } from '@playwright/test';

const ROUTINE_ID = 42;

function injectFakeAuth(page: Page) {
  return page.addInitScript(() => {
    const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
    const payload = btoa(JSON.stringify({ exp: 9999999999, sub: 'test-user' }));
    localStorage.setItem('jwtToken', `${header}.${payload}.fakesig`);
  });
}

function makeRoutine() {
  return {
    uid: ROUTINE_ID,
    name: 'Nightly rent audit',
    prompt: 'Review rent payments.',
    schedule: '0 0 * * *',
    scheduleDisplay: 'Every day at 12:00 AM',
    isDefault: false,
    enabled: true,
    state: 'active',
    repeat: null,
    completedCount: 0,
    nextRunAt: '2026-05-01T00:00:00Z',
    lastRunAt: null,
    lastStatus: null,
    lastOutput: null,
    simulatedAt: null,
    createdAt: '2026-04-24T00:00:00Z',
  };
}

interface GraphQLBody {
  query?: string;
  variables?: Record<string, unknown>;
}

function mockGraphQL(page: Page, onRoutineQuery: (vars: Record<string, unknown> | undefined) => void) {
  return page.route('**/graphql', async (route) => {
    const body = route.request().postDataJSON() as GraphQLBody | null;
    const query = body?.query ?? '';

    // Single-routine lookup — the one this regression targets.
    if (query.includes('query Routine(') || /routine\s*\(\s*uid\s*:/.test(query)) {
      onRoutineQuery(body?.variables);
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: { routine: makeRoutine() } }),
      });
      return;
    }

    // Other queries (tasks, houses, tenants, suggestions, conversations, …)
    // — return empty but well-formed shapes so AppContext boot doesn't error.
    const emptyResponses: Record<string, unknown> = {
      tasks: [],
      houses: [],
      tenants: [],
      vendors: [],
      suggestions: [],
      routines: [],
      conversations: [],
      documents: [],
    };
    for (const [key, value] of Object.entries(emptyResponses)) {
      if (query.includes(`${key} {`) || query.includes(`${key}(`)) {
        await route.fulfill({
          contentType: 'application/json',
          body: JSON.stringify({ data: { [key]: value } }),
        });
        return;
      }
    }

    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: {} }),
    });
  });
}

test.describe('RoutineDetail', () => {
  test('sends uid as Int and renders the routine', async ({ page }) => {
    await injectFakeAuth(page);

    const capturedVariables: Array<Record<string, unknown> | undefined> = [];
    await mockGraphQL(page, (vars) => capturedVariables.push(vars));

    await page.goto(`/routines/${ROUTINE_ID}`);

    // The routine title should appear — "Routine not found" would not
    // include this text.
    await expect(page.getByText('Nightly rent audit')).toBeVisible();
    await expect(page.getByText('Routine not found')).toHaveCount(0);

    // At least one Routine query must have fired with uid coerced to a
    // number. A string here would have produced the original bug.
    expect(capturedVariables.length).toBeGreaterThan(0);
    const first = capturedVariables[0];
    expect(first).toBeDefined();
    expect(typeof first!.uid).toBe('number');
    expect(first!.uid).toBe(ROUTINE_ID);
  });
});
