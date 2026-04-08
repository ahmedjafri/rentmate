/**
 * E2E tests for file attachment in the chat input.
 *
 * Verifies that selecting a file via the paperclip button shows an attachment
 * chip, the chip transitions from uploading to ready, the user can type a
 * message alongside attachments, and sending includes attachment references.
 */

import { test, expect, Page } from '@playwright/test';

// ─── Helpers ────────────────────────────────────────────────────────────────

function injectFakeAuth(page: Page) {
  return page.addInitScript(() => {
    const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
    const payload = btoa(JSON.stringify({ exp: 9999999999, sub: 'test-user' }));
    localStorage.setItem('jwtToken', `${header}.${payload}.fakesig`);
  });
}

function mockGraphQL(page: Page) {
  return page.route('**/graphql', async (route) => {
    const body = route.request().postDataJSON() as { query?: string } | null;
    const query = body?.query ?? '';
    if (query.includes('tasks(') || query.includes('tasks {')) {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { tasks: [] } }) });
    } else if (query.includes('houses') || query.includes('properties')) {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { houses: [] } }) });
    } else if (query.includes('tenants')) {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { tenants: [] } }) });
    } else if (query.includes('conversations')) {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: { conversations: [] } }) });
    } else {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ data: {} }) });
    }
  });
}

function mockOnboarding(page: Page) {
  return page.route('**/onboarding/state', async (route) => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ onboarding: null, llm_configured: true }),
    });
  });
}

function mockSettings(page: Page) {
  return page.route('**/settings', async (route) => {
    if (route.request().method() === 'GET') {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ api_key: '', model: 'test/model', base_url: '', autonomy: {} }),
      });
    } else {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ ok: true }) });
    }
  });
}

function mockChat(page: Page, reply: string) {
  return page.route('**/chat/send', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      headers: { 'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no' },
      body: `data: ${JSON.stringify({ type: 'done', reply, conversation_id: 'test-conv-1' })}\n\n`,
    });
  });
}

async function setupPage(page: Page) {
  await injectFakeAuth(page);
  await mockGraphQL(page);
  await mockOnboarding(page);
  await mockSettings(page);
}

/**
 * Programmatically select a file in the hidden file input.
 * React's synthetic onChange doesn't fire from Playwright's setInputFiles,
 * so we set the files property directly and dispatch native events.
 */
async function selectFile(page: Page, filename: string, content = 'fake-content') {
  await page.evaluate(({ name, data }) => {
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const dt = new DataTransfer();
    dt.items.add(new File([data], name, { type: 'application/pdf' }));
    Object.defineProperty(input, 'files', { value: dt.files, writable: true });
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }, { name: filename, data: content });
}

// ─── Tests ──────────────────────────────────────────────────────────────────

test.describe('File attachment in chat', () => {
  test('selecting a file shows an attachment chip', async ({ page }) => {
    await setupPage(page);
    let uploadCalled = false;
    await page.route('**/api/upload-document', async (route) => {
      uploadCalled = true;
      await new Promise(r => setTimeout(r, 300));
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ document_id: 'doc-test-1' }),
      });
    });
    await mockChat(page, 'Got it!');

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Paperclip button should be visible
    const paperclip = page.locator('button').filter({ has: page.locator('svg.lucide-paperclip') });
    await expect(paperclip).toBeVisible({ timeout: 5000 });

    // Select a file
    await selectFile(page, 'test-lease.pdf');

    // Chip should appear with filename
    await expect(page.getByText('test-lease.pdf')).toBeVisible({ timeout: 3000 });

    // Should show uploading state
    await expect(page.getByText('uploading…')).toBeVisible();

    // After upload completes, uploading disappears but chip remains
    await expect(page.getByText('uploading…')).not.toBeVisible({ timeout: 3000 });
    await expect(page.getByText('test-lease.pdf')).toBeVisible();

    // Placeholder changes to optional message
    await expect(page.getByPlaceholder('Add a message (optional)...')).toBeVisible();

    expect(uploadCalled).toBe(true);
  });

  test('can type a message while file is attached', async ({ page }) => {
    await setupPage(page);
    await page.route('**/api/upload-document', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ document_id: 'doc-test-2' }),
      });
    });
    await mockChat(page, 'Got it!');

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await selectFile(page, 'rental-agreement.pdf');
    await expect(page.getByText('rental-agreement.pdf')).toBeVisible({ timeout: 3000 });

    const textarea = page.getByPlaceholder('Add a message (optional)...');
    await textarea.fill('Here is my lease agreement');
    await expect(textarea).toHaveValue('Here is my lease agreement');
  });

  test('sending includes attachment reference and clears chips', async ({ page }) => {
    await setupPage(page);
    await page.route('**/api/upload-document', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ document_id: 'doc-test-3' }),
      });
    });

    let sentMessage = '';
    await page.route('**/chat/send', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const body = JSON.parse(route.request().postData() ?? '{}');
      sentMessage = body.message ?? '';
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache' },
        body: `data: ${JSON.stringify({ type: 'done', reply: 'Got it!', conversation_id: 'test-conv-1' })}\n\n`,
      });
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await selectFile(page, 'lease.pdf');
    await expect(page.getByText('lease.pdf')).toBeVisible({ timeout: 3000 });
    await expect(page.getByText('uploading…')).not.toBeVisible({ timeout: 2000 });

    // Type and send
    const textarea = page.getByPlaceholder('Add a message (optional)...');
    await textarea.fill('Please review');
    await textarea.press('Enter');

    // Verify message included attachment reference
    await page.waitForTimeout(500);
    expect(sentMessage).toContain('Please review');
    expect(sentMessage).toContain('[Attached documents:');
    expect(sentMessage).toContain('lease.pdf');

    // Chip should be gone after send
    await expect(page.getByText('lease.pdf').first()).not.toBeVisible({ timeout: 2000 });
  });

  test('can remove an attachment with X button', async ({ page }) => {
    await setupPage(page);
    await page.route('**/api/upload-document', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ document_id: 'doc-test-4' }),
      });
    });
    await mockChat(page, 'ok');

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await selectFile(page, 'remove-me.pdf');
    await expect(page.getByText('remove-me.pdf')).toBeVisible({ timeout: 3000 });

    // Click the remove button on the chip
    const chipContainer = page.getByText('remove-me.pdf').locator('..');
    await chipContainer.locator('button').click();

    await expect(page.getByText('remove-me.pdf')).not.toBeVisible();
  });

  test('sending with no text but attachment generates default message', async ({ page }) => {
    await setupPage(page);
    await page.route('**/api/upload-document', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ document_id: 'doc-test-5' }),
      });
    });

    let sentMessage = '';
    await page.route('**/chat/send', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      const body = JSON.parse(route.request().postData() ?? '{}');
      sentMessage = body.message ?? '';
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        headers: { 'Cache-Control': 'no-cache' },
        body: `data: ${JSON.stringify({ type: 'done', reply: 'Processing...', conversation_id: 'test-conv-1' })}\n\n`,
      });
    });

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await selectFile(page, 'my-lease.pdf');
    await expect(page.getByText('my-lease.pdf')).toBeVisible({ timeout: 3000 });
    await expect(page.getByText('uploading…')).not.toBeVisible({ timeout: 2000 });

    // Send without typing — click the send button
    const sendButton = page.locator('button').filter({ has: page.locator('svg.lucide-send') });
    await sendButton.click();

    await page.waitForTimeout(500);
    expect(sentMessage).toContain("I've uploaded: my-lease.pdf");
    expect(sentMessage).toContain('[Attached documents:');
  });

  test('duplicate upload still shows attachment chip', async ({ page }) => {
    await setupPage(page);
    await page.route('**/api/upload-document', async (route) => {
      await route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify({ document_id: 'doc-existing-1', duplicate: true }),
      });
    });
    await mockChat(page, 'Already have it!');

    await page.goto('/');
    await page.waitForLoadState('networkidle');

    await selectFile(page, 'already-uploaded.pdf');
    await expect(page.getByText('already-uploaded.pdf')).toBeVisible({ timeout: 3000 });
    await expect(page.getByText('uploading…')).not.toBeVisible({ timeout: 2000 });
  });
});
