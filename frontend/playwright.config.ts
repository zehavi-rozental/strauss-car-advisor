import { defineConfig, devices } from '@playwright/test'

// Browser e2e against the running dev servers (Vite proxies /api -> backend :8000).
// The BASE_URL env var points at the Vite dev server (it may pick 5173/5174/5175 if others are busy).
// Start the servers first (backend: uvicorn :8000, frontend: npm run dev), then: npx playwright test.
const BASE = process.env.E2E_BASE_URL || 'http://localhost:5173'

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,         // one conversation at a time (shared backend state per cookie, but serial is safer)
  workers: 1,
  retries: 0,
  // in CI also GitHub annotations: each failure (test, line, message) shows on the run page and in the API
  reporter: process.env.CI ? [['list'], ['github']] : [['list']],
  use: {
    baseURL: BASE,
    headless: true,
    actionTimeout: 15_000,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
