// Manual QA 15/11: a change made in "הנתונים שלי" or in the chat is confirmed once in the conversation (old and new
// value), every result is recalculated, and after a reload the same data, the same figures and the same conversation
// are there (it goes on, it does not start again). Synthetic data only; figures are compared with the server's.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const PAY = { base_salary: 18000, commissions: 0, bonus: 0, pension_percent: 6, study_fund_percent: 2.5, credit_points: 2.25,
              children_under_23: 2 }

async function idle(page: Page) {
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
}

async function send(page: Page, text: string) {
  await idle(page)
  const bots = await page.locator(BOT).count()
  const users = await page.locator(USER).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 45_000 }).toBeGreaterThan(users)
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
  await idle(page)
}

async function view(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await page.request.get(`/api/conversations/${id}`)).json()
}

const nets = async (page: Page) =>
  ((await view(page)).results as { label: string; result: { alternative: { net_display: string } } }[])
    .map((r) => `${r.label}:${r.result.alternative.net_display}`).join('|')

const updates = (page: Page) => page.locator(BOT).filter({ hasText: 'עדכנתי' })

async function toComparison(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await send(page, 'קבוצה ב')
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  await page.request.post(`/api/conversations/${id}/messages`, { data: { fields: PAY } })
  await page.reload()
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await send(page, 'יש לי קורולה קרוס, משלמת 300')
  if (!(await view(page)).today_copay) await send(page, '300')
  await send(page, 'תשווה בין MG4 ל-NIRO')
  await expect.poll(async () => (await view(page)).results.length, { timeout: 45_000 }).toBe(2)
}

test('a "הנתונים שלי" edit: one confirmation in the chat, recalculated, and the same after a reload', async ({ page }) => {
  await toComparison(page)
  const before = await nets(page)
  const count = await updates(page).count()
  await page.getByRole('button', { name: /הנתונים שלי/ }).click()
  const sheet = page.getByRole('dialog', { name: 'הנתונים שלי' })
  const bots = await page.locator(BOT).count()
  await sheet.getByRole('button', { name: 'לעדכן: שכר בסיס' }).click()
  await sheet.locator('.field-editor input').fill('20000')
  await sheet.getByRole('button', { name: 'לעדכן', exact: true }).click()
  await expect(sheet).toHaveCount(0, { timeout: 30_000 })
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBe(bots + 1)   // one reply
  await idle(page)
  await expect(page.locator(BOT).last()).toContainText('עדכנתי: שכר בסיס 20,000 ₪ במקום 18,000 ₪')
  await expect(updates(page)).toHaveCount(count + 1)
  await expect.poll(() => nets(page)).not.toBe(before)
  const after = await nets(page)
  const shown = await page.locator(BOT).count()

  await page.reload()                                   // the same conversation, data and figures; nothing said twice
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 30_000 }).toBe(shown)
  await expect(updates(page)).toHaveCount(count + 1)
  expect(await nets(page)).toBe(after)
  expect((await view(page)).profile.base).toBe('20000')
  await send(page, 'מה הכי משתלם?')                      // it goes on from where it was
  // #10 (27/11): the ranking's text says where the results are; how many were ranked is on the cards
  await expect(page.locator(BOT).last()).toContainText('התוצאות מוצגות בכרטיסים, מהנטו הגבוה לנמוך.')
})

test('a chat correction: saved, said once with the old value, every card recalculated', async ({ page }) => {
  await toComparison(page)
  const before = await nets(page)
  await send(page, 'אני משלמת 400 ולא 300')
  await expect(page.locator(BOT).last()).toContainText('השתתפות 400 ₪ במקום 300 ₪')
  await send(page, 'רגע, אני בקבוצה ג')
  await expect(page.locator(BOT).last()).toContainText('קבוצה ג במקום ב')
  const v = await view(page)
  expect(v.profile.group).toBe('ג')
  expect(v.today_copay).toBe('400')
  expect(v.results_stale).toBe(false)
  // the reply's figures are the cards' figures
  const card = page.locator('.summary-card:not(.map-card)').last()
  for (const r of v.results) await expect(card).toContainText(Number(r.result.alternative.net_display).toLocaleString('en-US'))
  expect(await nets(page)).toBe(before)                 // a copay of today's car changes the gap, not the alternative's net
})
