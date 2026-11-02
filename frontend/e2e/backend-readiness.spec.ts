import { expect, test, type Page, type Route } from '@playwright/test'

// The page is static and opens at once; the chat waits for the server and its database (shell/backendReady.ts).
// The server's states are simulated by intercepting GET /api/health in the browser, so this runs against any
// server (direct or through the edge Worker) and needs no sleeping backend. Every other request is real.

const BOT = '.bubble-bot .bubble-text'
const STATUS = '.service-status'
const WAKING = 'מכינים את יועץ הרכב. אפשר בינתיים להכיר את המסך.'

/** The first `n` health requests answer like a hosting layer that is still starting (no JSON); then the real one. */
async function asleepFor(page: Page, n: number, answer: (route: Route) => Promise<void>) {
  let calls = 0
  await page.route('**/api/health', async (route) => {
    calls += 1
    if (calls <= n) await answer(route)
    else await route.continue()
  })
  return () => calls
}

const wakingPage = (route: Route) => route.fulfill({ status: 503, contentType: 'text/html', body: '<html>starting</html>' })

test('a sleeping server: the page is there at once, the chat opens by itself when the server is ready', async ({ page }) => {
  const calls = await asleepFor(page, 3, wakingPage)
  let conversations = 0
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/api/conversations')) conversations++ })
  await page.goto('/employee')
  await expect(page.locator('.chat-header h1')).toHaveText('יועץ הרכב')          // the designed page, not a blank one
  await expect(page.locator(STATUS)).toContainText(WAKING)
  // the box stays usable (one may type ahead), but nothing can be sent before the server is ready
  await page.getByLabel('הודעה').fill('שלום')
  await expect(page.getByRole('button', { name: 'שליחה' })).toBeDisabled()
  expect(conversations).toBe(0)                                                   // nothing is opened before ready
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })        // no reload
  await expect(page.locator(STATUS)).toHaveCount(0)
  expect(calls()).toBe(5)                  // 3 asleep, the probe's ready answer, then the engine badge's own read
  expect(conversations).toBe(1)
})

test('an awake server shows no notice at all', async ({ page }) => {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator(STATUS)).toHaveCount(0)
})

test('the database is not reachable: "unavailable" with "לנסות שוב", never "waking"', async ({ page }) => {
  await asleepFor(page, 1, (route) => route.fulfill({ status: 200, contentType: 'application/json',
                                                       body: JSON.stringify({ status: 'degraded', db: false }) }))
  await page.goto('/employee')
  await expect(page.locator(STATUS)).toContainText('השירות אינו זמין כרגע')
  await expect(page.locator(STATUS)).not.toContainText(WAKING)
  await page.getByRole('button', { name: 'לנסות שוב' }).click()                  // the real health answers now
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator(STATUS)).toHaveCount(0)
})

test('a platform limit (429 without JSON) is "busy" and is not retried by itself', async ({ page }) => {
  const calls = await asleepFor(page, 99, (route) => route.fulfill({ status: 429, body: 'error code: 1027' }))
  await page.goto('/employee')
  await expect(page.locator(STATUS)).toContainText('יש כרגע עומס על השירות')
  await page.waitForTimeout(4_000)
  expect(calls()).toBe(1)
})

test('the rep screen waits too, and starts polling once ready', async ({ page }) => {
  await asleepFor(page, 2, wakingPage)
  let lists = 0
  page.on('request', (r) => { if (r.url().includes('/api/rep/questions')) lists++ })
  await page.addInitScript(() => sessionStorage.setItem('sca.repToken', 'not-checked-here'))
  await page.goto('/rep')
  await expect(page.locator(STATUS)).toContainText(WAKING)
  expect(lists).toBe(0)
  await expect.poll(() => lists, { timeout: 30_000 }).toBeGreaterThan(0)
})
