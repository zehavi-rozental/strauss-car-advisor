import { expect, test, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

// QA round 21/10: the two cloud conversations and the syncing between chat, form, payslip review, profile,
// results and the representative, in a real browser against the real server.
//
// The server runs WITHOUT an LLM (template mode) and with RECORDED payslip answers (PAYSLIP_FAKE_VISION_DIR,
// the same synthetic payslip that was used in the cloud: synthetic_payslip.pdf). These journeys test our
// code end to end; they are not a check of the live Gemini model.

const __dirname = dirname(fileURLToPath(import.meta.url))
const PAYSLIPS = join(__dirname, 'fixtures', 'payslips')
const REP_TOKEN = process.env.REP_TOKEN || ''
const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, text: string) {
  await expect(page.locator('.typing')).toHaveCount(0, { timeout: 45_000 })
  const users = await page.locator(USER).count()
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await expect(page.getByLabel('שליחה')).toBeEnabled({ timeout: 45_000 })
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 45_000 }).toBeGreaterThan(users)
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

const lastBot = (page: Page) => page.locator(BOT).last()

async function state(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const r = await page.request.get(`/api/conversations/${id}`)
  expect(r.status()).toBe(200)
  return r.json()
}

async function payslipEnabled(page: Page) {
  return (await (await page.request.get('/api/health')).json()).payslip === true
}

async function uploadPayslip(page: Page, file: string) {
  await page.getByRole('button', { name: 'להעלות תלוש' }).click()
  await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, file))
  await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
}

const row = (page: Page, field: string) => page.locator(`.payslip-row[data-field="${field}"]`)

/** The selection card stays where it is: open, the same cards, nothing moved. */
async function cardBoxes(page: Page) {
  return page.locator('.car-card').evaluateAll((els) => els.map((e) => {
    const b = e.getBoundingClientRect()
    return [Math.round(b.x), Math.round(b.y), Math.round(b.width)]
  }))
}

// ---------------------------------------------------------------- the two cloud conversations

test('cloud conversation 1: a company car, a mixed salary message, a fix, and "another car"', async ({ page }) => {
  await open(page)
  await send(page, 'יש לי רכב חברה ואני בקבוצה ב')
  await expect(lastBot(page)).toContainText('רשמתי שיש לכם רכב צמוד')
  await send(page, '16000')
  await send(page, 'אין עמלות')
  await send(page, 'בונוס-1000 פנסיה-5 קרן השתלמות-6\nאין נקודות זיכוי וילדים מתחת גיל 23')
  let s = await state(page)
  expect([s.profile.bonus, s.profile.pension_percent, s.profile.study_fund_percent, s.profile.children])
    .toEqual(['1000', '5', null, null])
  const reply = await lastBot(page).innerText()
  expect(reply).toContain('רשמתי: בונוס 1,000 ₪, פנסיה 5%')
  expect(reply).toContain('לא רשמתי קרן השתלמות 6%')
  expect(reply.split('\n').filter((l) => l.includes('?'))).toHaveLength(1)    // one question: 0 credit points?
  await expect(page.getByRole('button', { name: 'לתקן' })).toBeVisible()
  await page.getByRole('button', { name: 'לתקן' }).click()
  await expect(lastBot(page)).toContainText('כמה נקודות זיכוי מופיעות בתלוש?')
  await expect(lastBot(page)).not.toContainText('איזה רכב')
  await send(page, '3\nיש קורולה ומשלמת עליו אני לא זוכרת כמה')
  s = await state(page)
  expect([s.profile.credit_points, s.current_car.copay_unknown]).toEqual(['3', true])
  await page.getByRole('button', { name: 'COROLLA CROSS (2025)' }).click()
  await expect(lastBot(page)).toContainText('זיהיתי: COROLLA CROSS (2025)')
  await send(page, 'התכוונתי לרכב אחר')
  await expect(lastBot(page)).toContainText('מה הדגם של הרכב הצמוד שלכם')
  await expect(lastBot(page)).not.toContainText('אין רכב')
  await send(page, 'כתבתי לך שיש לי רכב למה אתה שואל שוב?')
  await expect(lastBot(page)).toContainText('יש לכם רכב צמוד, וחסר לי רק הדגם שלו')
  await expect(lastBot(page)).not.toContainText('נציג')
  await send(page, 'קורולה')
  await expect(lastBot(page)).toContainText('זיהיתי: COROLLA (2026)')     // the other Corolla, said explicitly
  s = await state(page)
  expect([s.current_car.label, s.current_car.has_car, s.current_car.copay_unknown])
    .toEqual(['COROLLA (2026)', true, true])
})

test('cloud conversation 2: no car, a payslip that differs, a salary correction, a result and the rep note', async ({ page, browser }) => {
  await open(page)
  test.skip(!(await payslipEnabled(page)), 'payslip extraction is off on this server')
  await send(page, 'אני בקבוצה ג, מרוויחה 15,000 ואין לי רכב היום')
  let s = await state(page)
  expect([s.profile.group, s.profile.base, s.current_car.source]).toEqual(['ג', '15000', 'none'])

  // the payslip (offered with the first salary question) says 18,000; the chat said 15,000: the employee chooses, nothing is saved before
  await uploadPayslip(page, 'synthetic_payslip.pdf')
  await expect(row(page, 'base_salary')).toContainText('התלוש שונה ממה שכתבתם בשיחה (15,000 ₪)')
  await page.getByRole('button', { name: /אישור ושמירה/ }).click()
  await expect(page.locator('.payslip-review')).toContainText('יש שורות שבהן צריך לבחור')
  expect((await state(page)).profile.base).toBe('15000')
  await row(page, 'base_salary').getByRole('radio', { name: 'מהתלוש: 18,000 ₪' }).check()
  await expect(page.locator('.payslip-info')).toContainText('תוספת חודשית')       // never a bonus on its own
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name: /אישור ושמירה/ }).click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
  await expect(lastBot(page)).toContainText('עדכנתי מהתלוש: שכר בסיס 18,000 ₪ במקום 15,000 ₪.')
  s = await state(page)
  expect([s.profile.base, s.profile.bonus, s.profile.commissions]).toEqual(['18000', null, null])

  await send(page, 'לא ידוע לי')
  expect((await state(page)).profile.unknown_fields.length).toBeLessThanOrEqual(1)   // never spread to all six
  await send(page, 'טעיתי, השכר שלי 16,000')
  expect((await state(page)).profile.base).toBe('16000')
  await send(page, 'אין עמלות, בונוס 5000, פנסיה 5%, קרן השתלמות 2%, 5 נקודות זיכוי, 3 ילדים')
  await send(page, '1000')                                              // the maintenance allowance
  await send(page, 'קורולה')
  s = await state(page)
  expect([s.profile.base, s.current_car.source, s.current_car.maintenance, s.selected])
    .toEqual(['16000', 'none', '1000', ['CAR-07']])
  expect(s.results).toHaveLength(1)
  expect(s.results_stale).toBe(false)

  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')
  const repContext = await browser.newContext()
  const rep = await repContext.newPage()
  await rep.goto('/rep')
  await rep.getByLabel('קוד גישה').fill(REP_TOKEN)
  await rep.getByRole('button', { name: 'כניסה' }).click()
  const card = rep.locator('.rep-card', { hasText: 'COROLLA (2026)' }).last()     // this conversation's (the newest)
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card).not.toContainText('נתוני השכר')                 // the notice line is gone; the data still is
  await expect(card).not.toContainText('16,000')
  await card.getByLabel('הערה לעובד').fill('האישור בתוקף מהחודש הבא')
  await card.getByRole('button', { name: /אישור/ }).first().click()
  await expect(page.locator('.event-pill').first()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator(BOT, { hasText: 'הערת הנציג: האישור בתוקף מהחודש הבא' })).toBeVisible()
  await repContext.close()          // the rep screen polls: an open one would use up the rep rate limit
})

// ---------------------------------------------------------------- general cases

test('keeping the chat value against the payslip keeps it, and the other approved rows are saved', async ({ page }) => {
  await open(page)
  test.skip(!(await payslipEnabled(page)), 'payslip extraction is off on this server')
  await send(page, 'מנהלת מוצר, שכר בסיס 17,000')
  await uploadPayslip(page, 'full_payslip.pdf')
  await row(page, 'base_salary').getByRole('radio', { name: 'מהשיחה: 17,000 ₪' }).check()
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name: 'אישור ושמירה (6)' }).click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
  const p = (await state(page)).profile
  expect([p.base, p.pension_percent, p.study_fund_percent, p.credit_points, p.children])
    .toEqual(['17000', '6', '2.5', '2.75', 2])
  await expect(page.locator(USER).last()).toContainText('מהתלוש (אושר)')
})

test('a rejected correction form keeps everything typed and names each refused field', async ({ page }) => {
  await open(page)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(page, m)
  await page.getByRole('button', { name: 'פירוט מלא' }).first().click()
  await page.getByRole('button', { name: 'תיקון נתונים' }).click()
  await expect(page.locator('#pf-bonus')).toHaveValue('0')                 // prefilled from the server
  await page.locator('#pf-bonus').fill('1000')
  await page.locator('#pf-pension_percent').fill('30')
  const form = page.locator('form[aria-label="תיקון נתונים"]')
  await form.getByRole('button', { name: 'שליחה' }).click()
  await expect(form).toContainText('פנסיה: הערך מחוץ לטווח')
  await expect(page.locator('#pf-bonus')).toHaveValue('1000')               // nothing typed is lost
  await expect(page.locator('#pf-pension_percent')).toHaveValue('30')
  expect((await state(page)).profile.bonus).toBe('0')                      // all or nothing: nothing saved
  await page.locator('#pf-pension_percent').fill('7')
  await form.getByRole('button', { name: 'שליחה' }).click()
  await expect.poll(async () => (await state(page)).profile.pension_percent).toBe('7')
  expect((await state(page)).profile.bonus).toBe('1000')
})

test('a result calculated before a change is marked out of date until it is recalculated', async ({ page }) => {
  await open(page)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(page, m)
  await expect(page.locator('.stale-note')).toHaveCount(0)
  await send(page, 'זה לא הרכב שלי')
  await expect(page.locator('.stale-note')).toContainText('לא מעודכן')
  expect((await state(page)).results_stale).toBe(true)
  await page.getByRole('button', { name: 'פירוט מלא' }).first().click()       // the full detail says so too
  await expect(page.getByRole('dialog').locator('.stale-note')).toContainText('לא מעודכן')
  await page.getByRole('button', { name: 'סגירה' }).click()
  await send(page, 'הכי משתלם')                                     // no ranking from an out-of-date result
  const waiting = await state(page)
  expect([waiting.results_stale, waiting.current_car.source]).toEqual([true, null])
  await send(page, 'קורולה 2026')
  await expect(page.locator('.stale-note')).toHaveCount(0)
  expect((await state(page)).results_stale).toBe(false)
})

for (const viewport of [{ width: 1280, height: 800 }, { width: 375, height: 740 }]) {
  test(`the selection card stays open and still while the page polls for the rep (${viewport.width}px)`, async ({ browser }) => {
    const context = await browser.newContext({ viewport })
    const page = await context.newPage()
    await open(page)
    for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(page, m)
    await expect(page.locator('.header-tag')).toContainText('ממתין לתשובת נציג')   // polling every 3 s
    await page.getByRole('button', { name: 'לשנות בחירה' }).click()
    const cards = page.locator('.car-card')
    await expect(cards.first()).toBeVisible()
    const before = await cardBoxes(page)
    for (let i = 0; i < 8; i++) {                                     // > two polls, with the mouse moving
      await page.mouse.move(100 + i * 20, 300 + (i % 2) * 15)
      await page.waitForTimeout(1000)
    }
    await expect(cards.first()).toBeVisible()
    expect(await cardBoxes(page)).toEqual(before)
    await cards.nth(1).click()                                        // still usable
    await expect(cards.nth(1)).toHaveAttribute('aria-pressed', 'true')
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow).toBeLessThanOrEqual(0)                           // no horizontal page scroll
    await context.close()
  })
}
