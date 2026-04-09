/**
 * Full-stack smoke test — no mocking.
 *
 * Verifies the entire auth → query → create → chat pipeline works end-to-end
 * against the real backend. Catches issues like missing creator context,
 * broken query filters, and auth chain failures.
 *
 * Run: npx playwright test --config playwright-e2e.config.ts tests/e2e/full-stack-smoke.spec.ts
 */

import { test, expect, Page } from '@playwright/test';

// ─── Helpers ────────────────────────────────────────────────────────────────

let cachedToken: string | null = null;

async function getToken(page: Page): Promise<string> {
  if (cachedToken) return cachedToken;
  const res = await page.request.post('/graphql', {
    data: {
      query: `mutation { login(input: { password: "rentmate" }) { token } }`,
    },
  });
  const body = await res.json();
  cachedToken = body.data?.login?.token;
  if (!cachedToken) throw new Error(`Login failed: ${JSON.stringify(body)}`);
  return cachedToken;
}

async function gql(page: Page, query: string, variables: Record<string, unknown> = {}) {
  const token = await getToken(page);
  const res = await page.request.post('/graphql', {
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    data: { query, variables },
  });
  expect(res.status()).toBe(200);
  const body = await res.json();
  if (body.errors?.length) {
    throw new Error(`GraphQL error: ${JSON.stringify(body.errors)}`);
  }
  return body.data;
}

async function rest(page: Page, method: string, path: string, body?: unknown) {
  const token = await getToken(page);
  const opts: Record<string, unknown> = {
    headers: { Authorization: `Bearer ${token}` },
  };
  if (body) {
    (opts.headers as Record<string, string>)['Content-Type'] = 'application/json';
    opts.data = body;
  }
  const res = method === 'GET'
    ? await page.request.get(path, opts)
    : await page.request.post(path, opts);
  return res;
}

// ─── Tests ──────────────────────────────────────────────────────────────────

test.describe('Full-stack smoke tests', () => {

  test('login returns a valid JWT', async ({ page }) => {
    const token = await getToken(page);
    expect(token).toBeTruthy();
    expect(token.split('.')).toHaveLength(3); // JWT has 3 parts
  });

  test('GraphQL queries return data (not 401 or 500)', async ({ page }) => {
    // Properties
    const props = await gql(page, '{ houses { uid address } }');
    expect(props.houses).toBeInstanceOf(Array);

    // Tenants
    const tenants = await gql(page, '{ tenants { uid firstName lastName } }');
    expect(tenants.tenants).toBeInstanceOf(Array);

    // Tasks
    const tasks = await gql(page, '{ tasks { uid title } }');
    expect(tasks.tasks).toBeInstanceOf(Array);

    // Suggestions
    const sugs = await gql(page, '{ suggestions { uid title } }');
    expect(sugs.suggestions).toBeInstanceOf(Array);

    // Conversations
    const convs = await gql(page, '{ conversations(conversationType: "user_ai") { uid title } }');
    expect(convs.conversations).toBeInstanceOf(Array);

    // Scheduled tasks
    const scheduled = await gql(page, '{ scheduledTasks { uid name schedule } }');
    expect(scheduled.scheduledTasks).toBeInstanceOf(Array);
  });

  test('can create a property via GraphQL', async ({ page }) => {
    const result = await gql(page,
      `mutation($input: CreatePropertyInput!) { createProperty(input: $input) { uid address } }`,
      { input: { address: 'E2E Test Property 123', propertyType: 'single_family' } }
    );
    expect(result.createProperty.uid).toBeTruthy();
    expect(result.createProperty.address).toContain('E2E Test');
  });

  test('REST settings endpoint returns 200', async ({ page }) => {
    const res = await rest(page, 'GET', '/settings');
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty('model');
  });

  test('REST onboarding endpoint returns 200', async ({ page }) => {
    const res = await rest(page, 'GET', '/onboarding/state');
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body).toHaveProperty('llm_configured');
  });

  test('chat send endpoint returns SSE stream (not 401)', async ({ page }) => {
    const token = await getToken(page);
    const res = await page.request.post('/chat/send', {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      data: { message: 'Hello, this is an e2e test' },
    });
    // Chat returns 200 with SSE stream, or agent error — but NOT 401
    expect(res.status()).not.toBe(401);
    expect(res.status()).not.toBe(500);
  });

  test('dashboard page loads without errors', async ({ page }) => {
    const token = await getToken(page);
    await page.addInitScript((t: string) => {
      localStorage.setItem('jwtToken', t);
    }, token);

    const errors: string[] = [];
    page.on('pageerror', err => errors.push(err.message));

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Should see the chat panel or dashboard content
    await expect(page.locator('body')).not.toBeEmpty();

    // No JS errors
    const critical = errors.filter(e =>
      !e.includes('ResizeObserver') && // benign browser warning
      !e.includes('Loading chunk') // vite HMR
    );
    expect(critical).toHaveLength(0);
  });

  test('documents page loads without errors', async ({ page }) => {
    const token = await getToken(page);
    await page.addInitScript((t: string) => {
      localStorage.setItem('jwtToken', t);
    }, token);

    const errors: string[] = [];
    page.on('pageerror', err => errors.push(err.message));

    await page.goto('/documents');
    await page.waitForLoadState('networkidle');

    const critical = errors.filter(e =>
      !e.includes('ResizeObserver') &&
      !e.includes('Loading chunk')
    );
    expect(critical).toHaveLength(0);
  });

  test('scheduled tasks page loads without errors', async ({ page }) => {
    const token = await getToken(page);
    await page.addInitScript((t: string) => {
      localStorage.setItem('jwtToken', t);
    }, token);

    const errors: string[] = [];
    page.on('pageerror', err => errors.push(err.message));

    await page.goto('/scheduled-tasks');
    await page.waitForLoadState('networkidle');

    // Should show scheduled tasks heading
    await expect(page.getByText('Scheduled Tasks')).toBeVisible({ timeout: 10000 });

    const critical = errors.filter(e =>
      !e.includes('ResizeObserver') &&
      !e.includes('Loading chunk')
    );
    expect(critical).toHaveLength(0);
  });
});
