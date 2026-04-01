import { defineConfig, devices } from '@playwright/test';

/**
 * E2E config that runs against the FULL stack (backend + frontend).
 * Requires `npm run dev` (which starts both backend on :8002 and Vite on :8080).
 *
 * Usage:
 *   npx playwright test --config playwright-e2e.config.ts
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 60_000,
  use: {
    baseURL: 'http://localhost:8080',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:8080',
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
