import { expect, test, type Page } from '@playwright/test'

// The interface additions of the redesign, checked against the real server (same session cookie):
// "תיקון נתונים" from the employee's last message saves through the server and recalculates every result, and a
// message lost on the way is sent again once at most (never twice, also when the server did get it).

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, text: string) {
  await expect(page.locator('.typing')).toHaveCount(0, { timeout: 45_000 })
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

async function state(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const r = await page.request.get(`/api/conversations/${id}`)
  expect(r.status()).toBe(200)
  return r.json()
}

const deltas = (s: { results: { option_id: string; result: { delta_display: string } }[] }) =>
  s.results.map((r) => [r.option_id, Number(r.result.delta_display)])

test('a correction from the last message is saved by the server and every result is recalculated', async ({ page }) => {
  await open(page)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(page, m)
  expect(deltas(await state(page))).toEqual([['CAR-10', 661]])

  // the pencil is on the employee's last message only
  const users = page.locator(USER)
  await expect(users.first().getByRole('button', { name: 'תיקון נתונים' })).toHaveCount(0)
  await users.last().hover()
  await users.last().getByRole('button', { name: 'תיקון נתונים' }).click()

  const form = page.locator('form[aria-label="תיקון נתונים"]')
  await expect(form.locator('#pf-base_salary')).toHaveValue('18500')      // prefilled from the server
  await form.locator('#pf-base_salary').fill('16000')
  const bots = await page.locator(BOT).count()
  await form.getByRole('button', { name: 'שליחה' }).click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)

  // saved by the server and recalculated by the engine (the same figure as typing the correction)
  const s = await state(page)
  expect(s.profile.base).toBe('16000')
  expect(deltas(s)).toEqual([['CAR-10', 656]])
  await expect(page.locator('.result-card').last()).toContainText('656 ₪')
  // the history is not rewritten: the sent message is still there as it was
  await expect(page.locator(USER, { hasText: 'שכר בסיס 18,500' })).toHaveCount(1)
})

test('a message lost on the way is sent again once', async ({ page }) => {
  await open(page)
  let fail = true
  await page.route('**/api/conversations/*/messages', (route) => (fail ? route.abort() : route.continue()))
  await page.getByLabel('הודעה').fill('מנהלת מוצר, שכר בסיס 18,500')
  await page.getByLabel('שליחה').click()
  await expect(page.locator(`${USER}.is-failed`)).toContainText('לא נשלחה')

  fail = false
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name: 'לשלוח שוב' }).click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
  await expect(page.locator(`${USER}.is-failed`)).toHaveCount(0)

  const s = await state(page)
  expect(s.profile.base).toBe('18500')
  expect(s.messages.filter((m: { role: string; content: string }) => m.role === 'user' && m.content.includes('18,500'))).toHaveLength(1)
})

test('when the server got the message but the answer was lost, "לשלוח שוב" does not send it twice', async ({ page }) => {
  await open(page)
  let lose = true
  await page.route('**/api/conversations/*/messages', async (route) => {
    if (!lose) return route.continue()
    await route.fetch()                 // the server receives and saves the message...
    return route.abort()                // ...but the browser never sees the answer
  })
  await page.getByLabel('הודעה').fill('מנהלת מוצר, שכר בסיס 18,500')
  await page.getByLabel('שליחה').click()
  await expect(page.locator(`${USER}.is-failed`)).toBeVisible()

  lose = false
  await page.getByRole('button', { name: 'לשלוח שוב' }).click()
  await expect(page.locator(`${USER}.is-failed`)).toHaveCount(0)
  await expect(page.locator(USER, { hasText: 'שכר בסיס 18,500' })).toHaveCount(1)

  const s = await state(page)
  expect(s.messages.filter((m: { role: string; content: string }) => m.role === 'user' && m.content.includes('18,500'))).toHaveLength(1)
  expect(s.profile.base).toBe('18500')
})
