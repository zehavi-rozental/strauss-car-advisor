// "הנתונים שלי" in the hotfix (9/11): the header button at the top left, the sheet with every value and its source, one value
// edited (salary, contributions, group, today's car, copay), the results recalculated and never shown stale as current,
// after a payslip too, on a desktop and at 375px. Synthetic data only; figures are compared with the server's.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const PAY = { base_salary: 18000, commissions: 0, bonus: 0, pension_percent: 6, study_fund_percent: 2.5, credit_points: 2.25,
              children_under_23: 2 }

async function waitBot(page: Page, bots: number) {
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
}

async function send(page: Page, text: string) {
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
  const bots = await page.locator(BOT).count()
  const users = await page.locator(USER).count()
  await page.getByLabel('הודעה').fill(text)
  await expect(page.getByLabel('שליחה')).toBeEnabled({ timeout: 45_000 })
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 45_000 }).toBeGreaterThan(users)
  await waitBot(page, bots)
}

async function view(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await page.request.get(`/api/conversations/${id}`)).json()
}

const today = async (page: Page) => (await view(page)).results[0]?.result.today.net_display as string | undefined

async function toResults(page: Page, source?: 'payslip') {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await send(page, 'קבוצה ג')
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  await page.request.post(`/api/conversations/${id}/messages`, { data: { fields: PAY, ...(source ? { source } : {}) } })
  await page.reload()
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await send(page, 'קורולה 2026')
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name: /זה הסכום שלי/ }).click()
  await waitBot(page, bots)
  const more = await page.locator(BOT).count()
  await page.getByRole('button', { name: /3 המשתלמים ביותר/ }).click()
  await waitBot(page, more)
  await expect(page.locator('.summary-card:not(.map-card) .summary-car')).toHaveCount(3, { timeout: 45_000 })
}

async function editValue(page: Page, label: string, value: string) {
  await page.getByRole('button', { name: /הנתונים שלי/ }).click()
  const sheet = page.getByRole('dialog', { name: 'הנתונים שלי' })
  const bots = await page.locator(BOT).count()
  await sheet.getByRole('button', { name: `לעדכן: ${label}` }).click()
  await sheet.locator('.field-editor input').fill(value)
  await sheet.getByRole('button', { name: 'לעדכן', exact: true }).click()
  await expect(sheet).toHaveCount(0, { timeout: 30_000 })
  await waitBot(page, bots)
}

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })

    test('the button sits under the blue band on the left and opens every value with its source', async ({ page }) => {
      await toResults(page)
      const button = page.getByRole('button', { name: /עריכת הנתונים שלי/ })
      const box = (await button.boundingBox())!
      const header = (await page.locator('.chat-header').boundingBox())!
      expect(box.x + box.width / 2).toBeLessThan(width / 2)
      expect(box.y).toBeGreaterThanOrEqual(header.y + header.height - 1)
      await expect(page.getByRole('button', { name: 'שיחה חדשה' })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await button.click()
      const sheet = page.getByRole('dialog', { name: 'הנתונים שלי' })
      for (const text of ['קבוצה ג', '18,000 ₪', 'COROLLA (2026)', '250 ₪', 'הקלדתם']) await expect(sheet).toContainText(text)
    })

    test('a salary edit recalculates and the comparison shows the new figures', async ({ page }) => {
      await toResults(page)
      const before = await today(page)
      await editValue(page, 'שכר בסיס', '22000')
      await expect(page.locator(BOT).last()).toContainText('עדכנתי: שכר בסיס 22,000 ₪ במקום 18,000 ₪')
      await expect.poll(() => today(page)).not.toBe(before)
      const v = await view(page)
      expect(v.results_stale).toBe(false)
      const card = page.locator('.summary-card:not(.map-card)').last()
      for (const r of v.results) await expect(card).toContainText(Number(r.result.alternative.net_display).toLocaleString('en-US'))
    })

    test('contributions and copay edits, then another car with its copay marked as carried', async ({ page }) => {
      await toResults(page)
      await editValue(page, 'פנסיה', '7')
      await expect(page.locator(BOT).last()).toContainText('פנסיה 7% במקום 6%')
      const before = Number(await today(page))
      await editValue(page, 'השתתפות נוכחית', '300')
      await expect(page.locator(BOT).last()).toContainText('השתתפות נוכחית 300 ₪ במקום 250 ₪')
      await expect.poll(async () => before - Number(await today(page))).toBe(50)
      await page.getByRole('button', { name: /הנתונים שלי/ }).click()
      const sheet = page.getByRole('dialog', { name: 'הנתונים שלי' })
      await sheet.getByRole('button', { name: 'לעדכן: הרכב של היום' }).click()
      await sheet.getByPlaceholder('למשל: קורולה').fill('נירו')
      const bots = await page.locator(BOT).count()
      await sheet.getByRole('button', { name: /NIRO/ }).first().click()
      await expect(sheet).toHaveCount(0, { timeout: 30_000 })
      await waitBot(page, bots)
      await expect(page.locator(BOT).last()).toContainText('נשארה מהרכב הקודם')
      await page.getByRole('button', { name: /הנתונים שלי/ }).click()
      await expect(page.getByRole('dialog', { name: 'הנתונים שלי' })).toContainText('נלקחה מהרכב הקודם')
    })
  })
}

test('after a payslip: its values say so, and an edit replaces one and recalculates', async ({ page }) => {
  await toResults(page, 'payslip')
  await page.getByRole('button', { name: /הנתונים שלי/ }).click()
  await expect(page.getByRole('dialog', { name: 'הנתונים שלי' })).toContainText('מהתלוש, באישורכם')
  await page.keyboard.press('Escape')
  const before = await today(page)
  await editValue(page, 'שכר בסיס', '21000')
  await expect.poll(() => today(page)).not.toBe(before)
})

test('a group edit is said, and an edit from an older screen asks to reload', async ({ page }) => {
  await toResults(page)
  await page.getByRole('button', { name: /הנתונים שלי/ }).click()
  const sheet = page.getByRole('dialog', { name: 'הנתונים שלי' })
  await sheet.getByRole('button', { name: 'לעדכן: קבוצת זכאות' }).click()
  const bots = await page.locator(BOT).count()
  await sheet.getByRole('button', { name: 'קבוצה ב' }).click()
  await waitBot(page, bots)
  await expect(page.locator(BOT).last()).toContainText('עדכנתי: קבוצה ב במקום ג')
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const stale = await page.request.post(`/api/conversations/${id}/messages`, { data: { fields: { base_salary: 30000 }, base_version: 0 } })
  expect(stale.status()).toBe(409)
})
