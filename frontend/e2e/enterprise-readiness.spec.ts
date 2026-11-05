// The fixes of 5/11, in the browser: one number for two fields is asked and answered ("2" → "לשניהם" / "בעצם רק ילדים"),
// one "הנתונים שלי" button and no value chips, the results read from their top after choosing three cars (desktop and
// phone), and the payslip uploader says what the server's policy says (no demo wording of its own). Synthetic data only.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const PROFILE = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, text: string) {
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
  const bots = await page.locator(BOT).count()
  const users = await page.locator(USER).count()
  await page.getByLabel('הודעה').fill(text)
  await expect(page.getByLabel('שליחה')).toBeEnabled({ timeout: 45_000 })
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 45_000 }).toBeGreaterThan(users)
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

async function profile(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const p = (await (await page.request.get(`/api/conversations/${id}`)).json()).profile
  return [p.credit_points, p.children].map((v: unknown) => (v == null ? null : String(v)))
}

test.describe('one number, two fields', () => {
  test('"2" is asked with a button per field and both; a later correction moves it', async ({ page }) => {
    await open(page)
    await send(page, 'קבוצה ב')
    await send(page, 'שכר בסיס 18000, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%')
    await expect(page.locator(BOT).last()).toContainText('נקודות זיכוי')
    await send(page, '2')
    await expect(page.locator(BOT).last()).toContainText('המספר 2 מתייחס לנקודות הזיכוי או למספר הילדים, או לשניהם?')
    await expect(page.locator(BOT).last()).not.toContainText('לא הצלחתי להבין')
    await page.getByRole('button', { name: 'לשניהם' }).click()
    await expect.poll(() => profile(page)).toEqual(['2', '2'])
    await send(page, 'בעצם רק ילדים')
    await expect(page.locator(BOT).last()).toContainText('רשום עכשיו רק למספר הילדים')
    await expect.poll(() => profile(page)).toEqual([null, '2'])
  })
})

async function chooseThree(page: Page) {
  await open(page)
  for (const m of [PROFILE, 'יש לי קורולה קרוס', 'ההשתתפות שלי 300']) await send(page, m)
  await page.getByRole('button', { name: /לבחור רכבים להשוואה/ }).first().click()
  const cards = page.locator('.selection [aria-pressed]')
  for (let i = 0; i < 3; i++) await cards.nth(i).click()
  await page.getByRole('button', { name: 'השוואה', exact: true }).click()
  await expect(page.locator('.summary-card .summary-car')).toHaveCount(3, { timeout: 45_000 })
}

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test(`after choosing three cars the results are read from their top (${width}px)`, async ({ browser }) => {
    const page = await (await browser.newContext({ viewport: { width, height } })).newPage()
    await chooseThree(page)
    const first = page.locator('.summary-card .summary-car').first()
    await expect(first).toBeInViewport({ timeout: 10_000 })
    // the comparison starts inside the visible chat area (not cut off under the header, not below the composer)
    const inside = async () => page.evaluate(() => {
      const body = document.querySelector('.chat-body')!.getBoundingClientRect()
      const card = document.querySelector('.summary-card')!.getBoundingClientRect()
      return card.top >= body.top - 12 && card.top < body.bottom      // its rounded top edge may tuck under the header
    })
    await expect.poll(inside).toBe(true)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.context().close()
  })
}

test('one "הנתונים שלי" button above the composer, and no separate value chips', async ({ page }) => {
  await open(page)
  await send(page, PROFILE)
  const button = page.getByRole('button', { name: /הנתונים שלי/ })
  await expect(button).toHaveCount(1)
  await expect(page.locator('.context-chip')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /שכר בסיס: 18,500/ })).toHaveCount(0)
  await button.click()
  await expect(page.getByRole('dialog', { name: 'הנתונים שלי' })).toContainText('18,500 ₪')
})

test('the payslip uploader says what the server says, and asks for the sample yes once', async ({ page, request }) => {
  const health = await (await request.get('/api/health')).json()
  test.skip(!health.payslip, 'payslip upload is off on this server')
  await open(page)
  await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const privacy = (await (await page.request.get(`/api/conversations/${id}`)).json()).privacy
  await page.getByRole('button', { name: 'להעלות תלוש' }).click()
  const uploader = page.getByRole('region', { name: 'העלאת תלוש' }).or(page.locator('[aria-label="העלאת תלוש"]'))
  await expect(uploader).toContainText(privacy.payslip.notice)
  await expect(uploader).not.toContainText('זו הדגמה')
  if (privacy.environment === 'test') {
    await expect(uploader).toContainText('סביבת בדיקות')
    await expect(page.getByRole('checkbox', { name: privacy.payslip.acknowledgement })).toBeVisible()
  }
})
