// No company car, missing data (19/11), in the browser against an isolated server (templates only, fake mail):
// the car-maintenance allowance is a closed question like today's copay; "לא ידוע" is a status (not asked again at once,
// completed at the end, never 0); a ranking asked before runs once the value is known; the representative is asked only
// from the inquiry card's own send, and the answer completes the value and recalculates. Synthetic data only.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const REP_TOKEN = process.env.REP_TOKEN || ''
const PROFILE = 'מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const PAY = { base_salary: 18500, commissions: 0, bonus: 0, pension_percent: 6, study_fund_percent: 2.5,
              credit_points: 2.75, children_under_23: 2 }
const CARDS = '.summary-card:not(.map-card) .summary-car'

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

async function click(page: Page, name: string | RegExp) {
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name }).last().click()
  await waitBot(page, bots)
}

async function view(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await page.request.get(`/api/conversations/${id}`)).json()
}

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })

    test('no payslip amount: nothing suggested; "לא ידוע" once, the ranking kept, "אין אחזקת רכב" at the end', async ({ page }) => {
      await open(page)
      await send(page, PROFILE)
      await send(page, 'הכי משתלם')
      await send(page, 'אין לי רכב')
      for (const label of ['להזין סכום', 'אין אחזקת רכב', 'לברר מול נציג']) {
        await expect(page.getByRole('button', { name: label })).toBeVisible()
      }
      await expect(page.getByRole('button', { name: 'זה הסכום שלי' })).toHaveCount(0)
      await send(page, 'לא ידוע')
      const status = page.locator(BOT).last()
      await expect(status).toContainText('החישוב מחכה רק לסכום אחזקת הרכב')
      await expect(status).not.toContainText('האם אתם מקבלים היום אחזקת רכב')   // not the question again at once
      expect((await view(page)).results).toEqual([])
      await send(page, 'לא ידוע')
      await expect(page.locator(BOT).last()).not.toContainText('לא הצלחתי להבין')
      await click(page, 'אין אחזקת רכב')
      await expect(page.locator(CARDS)).toHaveCount(3, { timeout: 45_000 })
      const v = await view(page)
      expect(v.stage).toBe('PRESENTED')
      expect(v.current_car.maintenance).toBe('0')
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    })

    test("a payslip line is offered; the representative is asked from the card, answers, and the net is calculated", async ({ page }) => {
      test.skip(!REP_TOKEN, 'REP_TOKEN')
      await open(page)
      await send(page, 'קבוצה ג')
      const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
      await page.request.post(`/api/conversations/${id}/messages`,
                              { data: { fields: PAY, source: 'payslip', payslip_maintenance: '1200' } })
      await page.reload()
      await expect(page.locator(BOT).last()).toBeVisible({ timeout: 30_000 })
      await send(page, 'הכי משתלם')
      await send(page, 'אין לי רכב')
      await expect(page.locator(BOT).last()).toContainText('זיהיתי אחזקת רכב של 1,200 ₪')
      for (const label of ['זה הסכום שלי', 'הסכום שונה', 'לברר מול נציג']) {
        await expect(page.getByRole('button', { name: label })).toBeVisible()
      }
      expect((await view(page)).current_car.maintenance).toBeNull()             // offered, not saved
      // the server is shared by the specs of one run: only this conversation's maintenance inquiry counts
      const rep = { 'X-Rep-Token': REP_TOKEN }
      const openMaintenance = async () => ((await (await page.request.get('/api/rep/questions?status=OPEN',
        { headers: rep })).json()) as { id: string; missing_field: string }[])
        .filter((q) => q.missing_field === 'MAINTENANCE_AMOUNT').map((q) => q.id)
      const before = await openMaintenance()
      await page.getByRole('button', { name: 'לברר מול נציג' }).last().click()
      // opening the card sends nothing: the employee reviews and sends it
      expect(await openMaintenance()).toEqual(before)
      const card = page.locator('.rep-inquiry-card')
      await expect(card).toBeVisible()
      await card.getByRole('button', { name: 'המשך', exact: true }).click()
      await expect(card).toContainText('אחזקה לפי התלוש')                        // the summary before sending
      expect(await openMaintenance()).toEqual(before)
      await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
      await expect.poll(async () => (await view(page)).current_car.maintenance_unknown, { timeout: 30_000 }).toBe(true)
      const created = (await openMaintenance()).filter((q) => !before.includes(q))
      expect(created).toHaveLength(1)                                            // exactly one, this conversation's
      expect((await view(page)).results).toEqual([])                            // no net while it is with the rep
      const answered = await page.request.post(`/api/rep/questions/${created[0]}/answer`,
                                               { headers: rep, data: { value: '1100' } })
      expect(answered.status()).toBe(200)
      await page.reload()
      await expect(page.locator(CARDS)).toHaveCount(3, { timeout: 45_000 })
      const v = await view(page)
      expect(v.current_car.maintenance).toBe('1100')
      expect(v.stage).toBe('PRESENTED')
    })
  })
}
