/**
 * E2E tests for the chat panel — both generic session chat and task chat.
 *
 * All backend endpoints are mocked via page.route() so the test suite runs
 * without a live server. The frontend Vite dev server must be running on
 * port 8080 (playwright.config.ts starts it automatically via webServer).
 */

import { test, expect, Page } from '@playwright/test';

// ─── Constants ────────────────────────────────────────────────────────────────

const TASK_ID = 'task-e2e-1';
const TASK_TITLE = 'HVAC Repair';

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Inject a fake JWT into localStorage before the page loads.
 * isAuthenticated() checks exp * 1000 < Date.now() — 9999999999 is ~year 2286.
 */
function injectFakeAuth(page: Page) {
  return page.addInitScript(() => {
    const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
    const payload = btoa(JSON.stringify({ exp: 9999999999, sub: 'test-user' }));
    localStorage.setItem('jwtToken', `${header}.${payload}.fakesig`);
  });
}

/** Minimal task shape returned by the GraphQL TASKS_QUERY mock. */
function makeApiTask(overrides: { messages?: ApiTaskMessage[] } = {}) {
  return {
    uid: TASK_ID,
    title: TASK_TITLE,
    isTask: true,
    taskStatus: 'active',
    taskMode: 'manual',
    category: 'maintenance',
    urgency: 'medium',
    confidential: false,
    createdAt: '2024-01-01T00:00:00Z',
    messages: overrides.messages ?? [],
  };
}

interface ApiTaskMessage {
  uid: string;
  body: string;
  messageType: string;
  senderName: string;
  isAi: boolean;
  isSystem: boolean;
  sentAt: string;
}

const AI_MESSAGE: ApiTaskMessage = {
  uid: 'ai-msg-1',
  body: 'The HVAC filter is scheduled for replacement next week.',
  messageType: 'message',
  senderName: 'RentMate',
  isAi: true,
  isSystem: false,
  sentAt: '2024-01-01T00:01:00Z',
};

/**
 * Intercept all POST /graphql requests. Routes by query content:
 *   - tasks(  → TASKS_QUERY / SUGGESTIONS_QUERY
 *   - task(   → TASK_QUERY (single task by uid)
 *   - houses  → HOUSES_QUERY
 *   - tenants → TENANTS_QUERY
 *   - mutations → return minimal success
 *
 * The optional `onTaskQuery` callback fires each time a single-task query
 * arrives, letting tests vary responses over multiple calls.
 */
function mockGraphQL(
  page: Page,
  options: {
    tasks?: ReturnType<typeof makeApiTask>[];
    onTaskQuery?: (callNumber: number) => ReturnType<typeof makeApiTask> | null;
  } = {},
) {
  let taskQueryCount = 0;

  return page.route('**/graphql', async (route) => {
    const body = route.request().postDataJSON() as { query?: string; variables?: Record<string, unknown> } | null;
    const query = body?.query ?? '';

    // Single-task lookup (TASK_QUERY)
    if (query.includes('task(uid:') || (query.includes('task(') && body?.variables?.uid)) {
      taskQueryCount++;
      const task = options.onTaskQuery
        ? options.onTaskQuery(taskQueryCount)
        : (options.tasks?.[0] ?? null);
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: { task } }),
      });
      return;
    }

    // Task list queries (TASKS_QUERY, SUGGESTIONS_QUERY)
    if (query.includes('tasks(') || query.includes('tasks {')) {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: { tasks: options.tasks ?? [] } }),
      });
      return;
    }

    // Property list
    if (query.includes('houses')) {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: { houses: [] } }),
      });
      return;
    }

    // Tenant list
    if (query.includes('tenants')) {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ data: { tenants: [] } }),
      });
      return;
    }

    // Mutations (addTaskMessage, updateTask, deleteTask, …) — return minimal success
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ data: {} }),
    });
  });
}

/**
 * Mock the generic POST /chat endpoint — now SSE like task chat.
 */
function mockGenericChat(page: Page, reply: string) {
  return page.route('**/chat', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' },
      body: sseBody([
        { type: 'progress', text: 'Thinking\u2026' },
        { type: 'done', reply, conversation_id: 'test-conv-1' },
      ]),
    });
  });
}

/**
 * Build an SSE body string from an array of event objects.
 */
function sseBody(events: Array<Record<string, unknown>>): string {
  return events.map(e => `data: ${JSON.stringify(e)}\n\n`).join('');
}

/**
 * Mock GET /chat/task/{taskId}/stream — used by the reconnect effect when a
 * task chat panel opens. Returns `idle` so the effect exits without interfering.
 */
function mockTaskStreamReconnect(page: Page, taskId: string) {
  return page.route(`**/chat/task/${taskId}/stream`, async (route) => {
    await route.fulfill({
      contentType: 'text/event-stream',
      body: sseBody([{ type: 'idle' }]),
    });
  });
}

/**
 * Mock POST /chat/task — the primary SSE endpoint for task messages.
 */
function mockTaskChat(page: Page, events: Array<Record<string, unknown>>) {
  return page.route('**/chat/task', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' },
      body: sseBody(events),
    });
  });
}

// ─── Tests ────────────────────────────────────────────────────────────────────

test.describe('Generic session chat', () => {
  test('sends a message and displays the AI reply', async ({ page }) => {
    await injectFakeAuth(page);
    await mockGraphQL(page);
    await mockGenericChat(page, 'Hello! How can I help you today?');

    await page.goto('/');

    // Open the generic chat panel via the header button
    await page.getByRole('button', { name: /ask rentmate/i }).click();

    // Panel is open — type a message
    const textarea = page.getByPlaceholder('Type a message...');
    await expect(textarea).toBeVisible();
    await textarea.fill('Hi there');

    // Send (Enter key)
    await textarea.press('Enter');

    // User message appears immediately
    await expect(page.getByText('Hi there')).toBeVisible();

    // AI reply should appear via SSE done event
    await expect(page.getByText('Hello! How can I help you today?')).toBeVisible({ timeout: 10_000 });
  });

  test('typing indicator shows while waiting for a reply', async ({ page }) => {
    await injectFakeAuth(page);
    await mockGraphQL(page);

    // Slow SSE response so the thinking row is visible long enough to assert
    await page.route('**/chat', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise(r => setTimeout(r, 800));
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' },
        body: sseBody([
          { type: 'progress', text: 'Thinking\u2026' },
          { type: 'done', reply: 'Sure thing.', conversation_id: 'test-conv-2' },
        ]),
      });
    });

    await page.goto('/');
    await page.getByRole('button', { name: /ask rentmate/i }).click();

    const textarea = page.getByPlaceholder('Type a message...');
    await textarea.fill('What can you do?');
    await textarea.press('Enter');

    // Thinking row must appear while the slow response is in-flight
    await expect(page.getByTestId('thinking-row')).toBeVisible({ timeout: 5_000 });

    // After the response arrives the thinking row disappears and reply shows
    await expect(page.getByTestId('thinking-row')).not.toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('Sure thing.')).toBeVisible();
  });
});

test.describe('Task chat — SSE stream', () => {
  test('shows progress events and displays the AI reply on done', async ({ page }) => {
    await injectFakeAuth(page);
    await mockGraphQL(page, { tasks: [makeApiTask()] });
    await mockTaskStreamReconnect(page, TASK_ID);
    await mockTaskChat(page, [
      { type: 'progress', text: 'Checking maintenance records…' },
      { type: 'progress', text: 'Drafting response…' },
      { type: 'done', reply: 'The HVAC filter is in good condition.', message_id: 'msg-1', actions: [] },
    ]);

    await page.goto('/action-desk');

    // Click the task card to open the chat panel
    await page.getByText(TASK_TITLE).first().click();

    // Type a message
    const textarea = page.getByPlaceholder('Type a message...');
    await expect(textarea).toBeVisible({ timeout: 5_000 });
    await textarea.fill('What is the status of the HVAC?');
    await textarea.press('Enter');

    // Progress indicator appears
    await expect(page.getByTestId('thinking-row')).toBeVisible({ timeout: 5_000 });

    // At least one progress line renders
    await expect(page.getByTestId('progress-line').first()).toBeVisible({ timeout: 5_000 });

    // Final AI reply appears after done event
    await expect(page.getByText('The HVAC filter is in good condition.')).toBeVisible({ timeout: 10_000 });

    // Typing indicator clears
    await expect(page.getByTestId('thinking-row')).not.toBeVisible({ timeout: 5_000 });

    // Reasoning trace is persisted in the thread as a collapsible internal message.
    // The ThinkingChain component renders "Thinking (N steps)" for multi-step traces.
    await expect(page.getByText(/thinking/i)).toBeVisible({ timeout: 5_000 });
  });

  test('shows an error message when the SSE stream returns an error event', async ({ page }) => {
    await injectFakeAuth(page);
    await mockGraphQL(page, { tasks: [makeApiTask()] });
    await mockTaskStreamReconnect(page, TASK_ID);
    await mockTaskChat(page, [
      { type: 'error', message: 'AI unavailable' },
    ]);

    await page.goto('/action-desk');
    await page.getByText(TASK_TITLE).first().click();

    const textarea = page.getByPlaceholder('Type a message...');
    await expect(textarea).toBeVisible({ timeout: 5_000 });
    await textarea.fill('Any updates?');
    await textarea.press('Enter');

    // Error should surface as a message in the thread
    await expect(
      page.getByText(/having trouble connecting|unavailable/i)
    ).toBeVisible({ timeout: 10_000 });
  });
});

test.describe('Task chat — SSE drop fallback', () => {
  test('loads AI reply from DB when SSE closes without a done event', async ({ page }) => {
    await injectFakeAuth(page);

    // First TASK_QUERY (initial refresh on task open): no messages yet.
    // Second TASK_QUERY (2 s fallback after SSE drop): has the AI reply.
    await mockGraphQL(page, {
      tasks: [makeApiTask()],
      onTaskQuery: (n) => makeApiTask({ messages: n >= 2 ? [AI_MESSAGE] : [] }),
    });

    await mockTaskStreamReconnect(page, TASK_ID);

    // SSE stream: sends only a progress event then closes — no `done`
    await mockTaskChat(page, [
      { type: 'progress', text: 'Thinking…' },
      // stream ends here without a done event
    ]);

    await page.goto('/action-desk');
    await page.getByText(TASK_TITLE).first().click();

    const textarea = page.getByPlaceholder('Type a message...');
    await expect(textarea).toBeVisible({ timeout: 5_000 });
    await textarea.fill('What is scheduled?');
    await textarea.press('Enter');

    // The AI message from the DB must appear within ~4 s
    // (2 s delay + GraphQL round-trip + render)
    await expect(
      page.getByText(AI_MESSAGE.body)
    ).toBeVisible({ timeout: 8_000 });
  });
});
