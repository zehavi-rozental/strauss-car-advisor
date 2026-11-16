// Manual QA 9/11, in the browser: the path that looped on production's code ("3 המשתלמים ביותר" alternating with "עדיין
// חסר לי: הרכבים להשוואה" after maintenance "לא יודעת"), three cars chosen with that value unknown, both contributions
// "none" while the car is asked, the restored "בירור אישי מול הנציג" on a new car's copay (sent, answered, applied), and
// car names that never break inside on a phone. Synthetic data only; figures are the server's.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const REP_TOKEN = process.env.REP_TOKEN
const LOOP = 'עדיין חסר לי: הרכבים להשוואה'
const PAY = { base_salary: 18500, commissions: 0, bonus: 0, credit_points: 2.75, children_under_23: 2 }

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

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

/** The payslip review's request (the same {fields, source} the screen sends), then the page as it is after it. */
async function payslipFields(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  await page.request.post(`/api/conversations/${id}/messages`, { data: { fields: PAY, source: 'payslip' } })
  await page.reload()
  await expect(page.locator(BOT).last()).toContainText('אילו הפרשות יורדות לכם', { timeout: 30_000 })
}

/** The manual path of 9/11, up to the cars: no car today, maintenance "לא יודעת". */
async function toTheCars(page: Page) {
  await open(page)
  await send(page, 'ב')
  await send(page, '18000')
  await payslipFields(page)
  await send(page, 'אין')
  await click(page, 'לאשר')
  await send(page, 'אין')
  await click(page, /אין לי רכב צמוד היום/)
  await send(page, 'אני לא יודעת')
  await expect(page.getByRole('button', { name: /3 המשתלמים ביותר/ })).toBeVisible()
}

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })

    test('"3 המשתלמים ביותר" with a value unknown keeps the request, asks it with where it is, then ranks', async ({ page }) => {
      await toTheCars(page)
      await click(page, /3 המשתלמים ביותר/)
      const reply = page.locator(BOT).last()
      await expect(reply).toContainText('ביקשתם את החלופות הכי משתלמות')
      await expect(reply).toContainText('חסר לי עוד סכום אחזקת הרכב')
      await expect(reply).toContainText('שורת אחזקת רכב')
      await expect(reply).not.toContainText(LOOP)
      await expect(page.getByRole('button', { name: /3 המשתלמים ביותר/ })).toHaveCount(0)   // recorded: not offered again
      await send(page, 'אין')
      await expect(page.locator('.summary-card:not(.map-card) .summary-car')).toHaveCount(3, { timeout: 45_000 })
      expect((await view(page)).stage).toBe('PRESENTED')
      expect(await page.locator(BOT).allInnerTexts()).not.toContain(expect.stringContaining(LOOP))
    })

    test('three cars chosen with the value unknown: one question with why, then the comparison of those three', async ({ page }) => {
      await toTheCars(page)
      await page.getByRole('button', { name: /לבחור רכבים להשוואה/ }).first().click()
      const cards = page.locator('.selection [aria-pressed="false"]:not([disabled])')
      for (let i = 0; i < 3; i++) await cards.first().click()
      const bots = await page.locator(BOT).count()
      await page.getByRole('button', { name: 'השוואה', exact: true }).click()
      await waitBot(page, bots)
      await expect(page.locator(BOT).last()).toContainText('חסר לי עוד סכום אחזקת הרכב')
      await expect(page.locator(BOT).last()).not.toContainText('אפשר גם לבדוק בהמשך')
      const chosen = (await view(page)).expects
      expect(chosen).toBeTruthy()
      await send(page, '1,200')
      await expect(page.locator('.summary-card:not(.map-card) .summary-car')).toHaveCount(3, { timeout: 45_000 })
      // the names on the line above the buttons: each one whole on its line (9/11: "BYD" / "DOLPHIN (2025)")
      const names = page.locator('.car-actions-current .car-name')
      await expect(names).toHaveCount(3)
      for (const box of await names.evaluateAll((els) => els.map((e) => e.getClientRects().length))) expect(box).toBe(1)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    })

    test('both contributions "none" while the car is asked are confirmed and kept', async ({ page }) => {
      await open(page)
      await send(page, 'ב')
      await send(page, '18000')
      await payslipFields(page)
      await send(page, 'אין הפרשות לפנסיה ולקרן השתלמות')
      await expect(page.locator(BOT).last()).toContainText('לרשום פנסיה 0% וקרן השתלמות 0%')
      await click(page, 'לאשר')
      const p = (await view(page)).profile
      expect([Number(p.pension_percent), Number(p.study_fund_percent)]).toEqual([0, 0])
    })
  })
}

test('"בירור אישי מול הנציג" on a new car\'s copay: sent for that car, answered, applied', async ({ page }) => {
  test.skip(!REP_TOKEN, 'REP_TOKEN')
  await open(page)
  await send(page, 'קבוצה ג')
  await send(page, 'שכר בסיס 18,000, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 2 ילדים')
  await send(page, 'קורולה 2026')
  await click(page, /זה הסכום שלי/)
  await click(page, /3 המשתלמים ביותר/)
  const links = page.getByRole('button', { name: /בירור אישי מול הנציג/ })
  await expect(links).toHaveCount(3)                                   // one per new car
  const first = (await view(page)).results[0]
  const rep = { 'X-Rep-Token': REP_TOKEN! }
  const openNow = async () => (await (await page.request.get('/api/rep/questions?status=OPEN', { headers: rep })).json()) as
    { id: string; missing_field: string; option_id: string }[]
  const earlier = new Set((await openNow()).map((q) => q.id))     // leftovers of earlier runs may be open for the same car
  await links.first().click()
  const card = page.getByRole('region', { name: 'העברת שאלה לנציג הרכב' }).or(page.locator('section[aria-label="העברת שאלה לנציג הרכב"]'))
  await expect(card).toContainText(first.label)
  await card.getByRole('button', { name: 'המשך' }).click()
  await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
  await expect(page.locator('[data-testid="rep-inquiry-history"]').last()).toContainText(first.label, { timeout: 30_000 })
  await expect(links).toHaveCount(2)                                   // the asked car shows its status, no second link
  const mine = (await openNow()).filter((x) => !earlier.has(x.id) && x.missing_field === 'NEW_CAR_COPAY' && x.option_id === first.option_id)
  expect(mine).toHaveLength(1)                                         // this conversation's own question, by its new id
  const q = mine[0]
  const answered = await page.request.post(`/api/rep/questions/${q.id}/answer`, { headers: rep, data: { value: '230' } })
  expect(answered.status()).toBe(200)
  await expect.poll(async () => {
    const r = (await view(page)).results.find((x: { option_id: string }) => x.option_id === first.option_id)
    return [r?.result.copay_source, r?.result.alternative?.copay]
  }, { timeout: 30_000 }).toEqual(['representative', '230'])
})

test('production 9/11: "2" for the contributions, then "לשתיהם" saves 2% to both (buttons shown, nothing lost)', async ({ page }) => {
  await open(page)
  await send(page, 'ב')
  await send(page, '18000')
  await payslipFields(page)                                    // the contributions are the question on screen
  await send(page, '2')
  await expect(page.locator(BOT).last()).toContainText('לשניהם')
  await expect(page.getByRole('button', { name: 'לשניהם' })).toBeVisible()
  await send(page, 'לשתיהם')
  await expect(page.locator(BOT).last()).toContainText('רשמתי: פנסיה 2%, קרן השתלמות 2%')
  await expect(page.locator(BOT).last()).not.toContainText('לא בטוח שהבנתי')
  const p = (await view(page)).profile
  expect([Number(p.pension_percent), Number(p.study_fund_percent)]).toEqual([2, 2])
})
