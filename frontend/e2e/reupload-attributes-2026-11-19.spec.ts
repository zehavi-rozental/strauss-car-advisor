// A repeated payslip upload and car properties the catalog cannot verify (19/11), in the browser against an isolated
// server (templates only; payslips from RECORDED answers; fake mail). Desktop and phone. Synthetic data only.
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const PAYSLIPS = join(dirname(fileURLToPath(import.meta.url)), 'fixtures', 'payslips')
const REP_TOKEN = process.env.REP_TOKEN || ''
const UPLOAD_BTN = '.persistent-actions .link-payslip_upload, .answer-tail .link-button.link-payslip_upload'
const PAY = { base_salary: 18500, commissions: 0, bonus: 0, pension_percent: 6, study_fund_percent: 2.5,
              credit_points: 2.75, children_under_23: 2 }

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

async function state(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await page.request.get(`/api/conversations/${id}`)).json()
}

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

/** The profile as a reviewed payslip would give it, then no car and no allowance: the alternatives are asked. */
async function atTheCars(page: Page) {
  await open(page)
  await send(page, 'קבוצה ג')
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  await page.request.post(`/api/conversations/${id}/messages`, { data: { fields: PAY, source: 'payslip' } })
  await page.reload()
  await expect(page.locator(BOT).last()).toBeVisible({ timeout: 30_000 })
  await send(page, 'אין לי רכב')
  await send(page, 'אין')
}

async function openRep(page: Page, text: string) {
  const res = await page.request.get('/api/rep/questions?status=OPEN', { headers: { 'X-Rep-Token': REP_TOKEN } })
  return ((await res.json()) as { question_text: string }[]).filter((q) => q.question_text.includes(text))
}

const fitsWidth = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })

    test('a colour request: unverified, a draft with the request itself, sent once, the cars go on', async ({ page }) => {
      test.skip(!REP_TOKEN, 'REP_TOKEN')
      await atTheCars(page)
      // the server is shared by the runs of this spec: only this conversation's inquiry counts
      const base = (await openRep(page, 'בצבע אדום')).length
      await send(page, 'אני רוצה רכב בצבע אדום')
      const reply = page.locator(BOT).last()
      await expect(reply).toContainText('לא מופיעים בקטלוג שלי')
      await expect(reply).not.toContainText('איזה רכבים להשוות')                 // a way to handle it first
      const ask = page.getByRole('button', { name: 'לשאול את נציג הרכב' })
      const skip = page.getByRole('button', { name: 'להמשיך בלי' })
      await expect(ask).toBeVisible()
      await expect(skip).toBeVisible()
      expect(await fitsWidth(page)).toBe(true)
      // the two buttons are whole on the screen, side by side or wrapped, never overlapping
      const a = (await ask.boundingBox())!
      const b = (await skip.boundingBox())!
      expect(a.x >= 0 && a.x + a.width <= width && b.x >= 0 && b.x + b.width <= width).toBe(true)
      expect(a.y + a.height <= b.y || b.y + b.height <= a.y || a.x + a.width <= b.x || b.x + b.width <= a.x).toBe(true)
      // the follow-up: the request is the draft, never "write it again"
      await send(page, 'אין לך איך לשאול נציג על זה?')
      await expect(page.locator(BOT).last()).toContainText('אני רוצה רכב בצבע אדום')
      expect(await openRep(page, 'בצבע אדום')).toHaveLength(base)                 // nothing sent by itself
      await page.getByRole('button', { name: 'לשאול את נציג הרכב' }).click()
      const card = page.locator('.rep-inquiry-card')
      await expect(card.locator('textarea')).toHaveValue(/אני רוצה רכב בצבע אדום/)
      await card.getByRole('button', { name: 'המשך', exact: true }).click()
      await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
      await expect.poll(async () => (await openRep(page, 'בצבע אדום')).length, { timeout: 20_000 }).toBe(base + 1)
      // asked again: its status, never a second inquiry
      await send(page, 'אפשר לשאול נציג על זה?')
      expect(await openRep(page, 'בצבע אדום')).toHaveLength(base + 1)
      // the cars go on meanwhile, the inquiry is kept
      await send(page, 'אטו 3')
      await expect.poll(async () => (await state(page)).stage, { timeout: 30_000 }).toBe('PRESENTED')
      expect(await openRep(page, 'בצבע אדום')).toHaveLength(base + 1)
      expect(await fitsWidth(page)).toBe(true)
    })

    test('a repeated payslip: hidden after the first one, back on request, the change approved and recalculated', async ({ page }) => {
      test.skip((await (await page.request.get('/api/health')).json()).payslip !== true, 'payslip extraction is off')
      await open(page)
      await expect(page.locator(UPLOAD_BTN).first()).toBeVisible()                   // before: clear and available
      await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
      await page.locator(UPLOAD_BTN).first().click()
      await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'full_payslip.pdf'))
      await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
      let bots = await page.locator(BOT).count()
      await page.getByRole('button', { name: /אישור ושמירה/ }).click()
      await waitBot(page, bots)
      await expect(page.locator(UPLOAD_BTN)).toHaveCount(0)                          // confirmed: hidden
      await send(page, 'אין לי רכב')
      await send(page, 'אין')
      await send(page, 'אטו 3')
      await expect.poll(async () => (await state(page)).results.length, { timeout: 30_000 }).toBe(1)
      const before = (await state(page)).results[0].result.today.net_display
      await send(page, 'אני רוצה להעלות תלוש חדש')
      await expect(page.locator(BOT).last()).toContainText('כל שינוי')
      await expect(page.locator(UPLOAD_BTN).first()).toBeVisible()                   // back on request
      await page.locator(UPLOAD_BTN).first().click()
      await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'synthetic_payslip.pdf'))
      const base = page.locator('.payslip-row[data-field="base_salary"]')
      await expect(base).toContainText('שונה ממה שכתבתם בשיחה')                     // the change shown first
      expect((await state(page)).profile.base).toBe('18500')                        // nothing replaced yet
      await base.locator('input[type="radio"][value="payslip"]').check()
      bots = await page.locator(BOT).count()
      await page.getByRole('button', { name: /אישור ושמירה/ }).click()
      await waitBot(page, bots)
      await expect(page.locator(BOT).last()).toContainText('18,000 ₪ במקום 18,500 ₪')
      const s = await state(page)
      expect(s.profile.base).toBe('18000')
      expect(s.results[0].result.today.net_display).not.toBe(before)                // recalculated
      await expect(page.locator(UPLOAD_BTN)).toHaveCount(0)                          // hidden again
      expect(await fitsWidth(page)).toBe(true)
    })
  })
}
