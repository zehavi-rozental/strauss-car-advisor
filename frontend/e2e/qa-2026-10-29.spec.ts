import { expect, test } from '@playwright/test'
import type { Browser, BrowserContext, Locator, Page } from '@playwright/test'
import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

// Stage 7A of the 29/10 plan: real browser checks of the representative flow, after stages 1-6.
// Two local servers (isolated temporary SQLite, no LLM key): E2E_BASE_URL with EMAIL_BACKEND=fake (messages written
// to EMAIL_FAKE_DIR), OFF_BASE_URL with mail disabled. Screenshots go to SHOTS_DIR. Serial: one employee story.

const BASE = process.env.E2E_BASE_URL || ''
const OFF = process.env.OFF_BASE_URL || ''
const REP_TOKEN = process.env.REP_TOKEN || ''
const MAIL = process.env.EMAIL_FAKE_DIR || ''
const SHOTS = process.env.SHOTS_DIR || ''
const BOT = '.bubble-bot .bubble-text'
const RECORD = '[data-testid="rep-inquiry-history"]'
const EMAIL = 'qa.employee@example.test'
const PROFILE = 'מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const CAR = 'יש לי קורולה קרוס'
const GENERAL_ANSWER = 'כן, יש גרסה עם גג נפתח.\nהתוספת מטופלת מול היבואן.'

test.describe.configure({ mode: 'serial' })
test.skip(!BASE || !OFF || !REP_TOKEN || !MAIL || !SHOTS, 'E2E_BASE_URL, OFF_BASE_URL, REP_TOKEN, EMAIL_FAKE_DIR, SHOTS_DIR')
// 1/11: the car cards carry no inquiry link; the one place is today's car card above the comparison. The steps
// built on per-car inquiries (4, 6-9) are skipped (test.skip) for that reason; 1-2 cover the inquiry from today's
// copay question and the comparison that goes on while it waits

let employee: Page
const shot = (page: Page, name: string) => page.screenshot({ path: join(SHOTS, `${name}.png`), fullPage: true })

// Every browser context this file opens is recorded and closed, also when a test fails: what one test opens for
// itself (a rep screen, another device, the mail-off employee) closes in afterEach, right when that test ends; the
// employee page the tests share closes in afterAll. A rep screen left open keeps polling the rep API from this
// address and uses up the rate-limit budget of the specs that run after this one (CI runs them all on one server).
const perTest: BrowserContext[] = []
const perFile: BrowserContext[] = []
const opened = new Set<BrowserContext>()

async function newPage(browser: Browser, options: Parameters<Browser['newContext']>[0],
                       scope: 'test' | 'file' = 'test'): Promise<Page> {
  const context = await browser.newContext(options)
  opened.add(context)
  ;(scope === 'file' ? perFile : perTest).push(context)
  return context.newPage()
}

const closeAll = (list: BrowserContext[]) => Promise.all(list.splice(0).map((c) => c.close().catch(() => undefined)))
test.afterEach(async () => { await closeAll(perTest) })
test.afterAll(async ({ browser }) => {
  await closeAll(perTest)
  await closeAll(perFile)
  // nothing this file opened is still open (and polling) for the specs that run after it
  expect(browser.contexts().filter((c) => opened.has(c)), 'contexts left open by this file').toEqual([])
})

async function send(page: Page, text: string) {
  const n = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(n)
}

async function open(browser: Browser, base: string, width = 1280, scope: 'test' | 'file' = 'test') {
  const page = await newPage(browser, { baseURL: base, viewport: { width, height: 900 } }, scope)
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  return page
}

/** No horizontal page scroll, and no car name cut inside its own box. */
async function noOverflow(page: Page) {
  const wide = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
  expect(wide, 'horizontal page scroll').toBeLessThanOrEqual(1)
  const cut = await page.locator('.car-title-name, .latin').evaluateAll((els) =>
    els.filter((e) => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflow !== 'visible')
      .map((e) => e.textContent))
  expect(cut, 'car names cut').toEqual([])
}

// the fake mail folder may hold an earlier run's messages: only the ones written during this run are counted
let mailBefore = new Set<string>()

async function mails(): Promise<{ name: string; to: string; subject: string; body: string }[]> {
  const files = (await readdir(MAIL)).filter((f) => f.endsWith('.json') && !mailBefore.has(f))
  return Promise.all(files.map(async (name) => ({ name, ...JSON.parse(await readFile(join(MAIL, name), 'utf8')) })))
}

const token = (body: string) => body.match(/return_token=([A-Za-z0-9_-]+)/)?.[1] ?? ''
const row = (page: Page, car: string): Locator => page.locator('.summary-row', { hasText: car })

async function repScreen(browser: Browser, base: string) {
  const page = await newPage(browser, { baseURL: base, viewport: { width: 1280, height: 1000 } })
  await page.goto('/rep')
  await page.getByLabel('קוד גישה').fill(REP_TOKEN)
  await page.getByRole('button', { name: 'כניסה' }).click()
  return page
}

test('1. today copay asked through "לברר מול נציג": the address first, sent, and the comparison goes on', async ({ browser }) => {
  mailBefore = new Set((await readdir(MAIL)).filter((f) => f.endsWith('.json')))
  employee = await open(browser, BASE, 1280, 'file')     // the one employee story the tests share
  await send(employee, PROFILE)
  await send(employee, CAR)
  // today's copay is not known: the catalog amount is offered with three choices; the inquiry is one of them
  await employee.getByRole('button', { name: 'לברר מול נציג' }).click()
  const card = employee.locator('.rep-inquiry-card')
  await expect(card).toBeInViewport()
  const email = card.getByLabel('כתובת אימייל לעדכון כשהנציג יענה')
  await expect(email).toBeVisible()                                                       // before "המשך"
  await expect(card.getByLabel('מה תרצו לשאול?')).toHaveValue(/COROLLA CROSS/)
  await card.getByRole('button', { name: 'המשך' }).click()
  await expect(card.locator('.rep-inquiry-field-error')).toBeVisible()                    // next to the address
  await email.fill(EMAIL)
  await card.getByRole('button', { name: 'המשך' }).click()
  await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
  await expect(employee.locator(RECORD)).toHaveCount(1)
  await send(employee, 'NIRO ו-DOLPHIN')
  await expect(employee.locator('.summary-card')).toBeVisible({ timeout: 30_000 })
  await expect(row(employee, 'NIRO')).toBeVisible()
  await expect(row(employee, 'DOLPHIN')).toBeVisible()
  await noOverflow(employee)
  await shot(employee, '01-summary-desktop')
  await employee.setViewportSize({ width: 375, height: 812 })
  await noOverflow(employee)
  // the copay line sits under its row, not squeezed into the rank column (D1)
  const copay = row(employee, 'NIRO').locator('.summary-copay')
  await expect(copay).toBeVisible()
  const box = await copay.boundingBox()
  expect(box!.width, 'copay line width at 375px').toBeGreaterThan(150)
  // the "ask" link on the dark leading row is light (readable), not the default blue (7B)
  const lead = employee.locator('.summary-row.is-best .link-inline')
  if (await lead.count()) {
    const rgb = (await lead.first().evaluate((e) => getComputedStyle(e).color)).match(/\d+/g)!.map(Number)
    expect((rgb[0] + rgb[1] + rgb[2]) / 3, 'link brightness on the dark row').toBeGreaterThan(170)
  }
  // a car name with its prefix moves as one unit, never wider than its bubble
  const runs = await employee.locator('.bubble .latin-run').evaluateAll((els) =>
    els.map((e) => e.getBoundingClientRect().width <= (e.closest('.bubble-text') as HTMLElement).clientWidth + 1))
  expect(runs.length).toBeGreaterThan(0)
  expect(runs.every(Boolean)).toBe(true)
  await shot(employee, '02-summary-375')
  await employee.setViewportSize({ width: 1280, height: 900 })
})

test('2. the comparison on the provisional catalog amount: one record, its status, no per-car link', async () => {
  await expect(employee.getByRole('button', { name: /בירור אישי/ })).toHaveCount(0)         // no per-car link
  await expect(employee.locator(RECORD).first()).toContainText('COROLLA CROSS')
  await expect(employee.locator(RECORD).first()).toContainText('ממתינה לתשובת הנציג')
  const today = employee.locator('.today-copay-card')
  await expect(today).toContainText('COROLLA CROSS')
  await expect(today).toContainText('נשלחה פנייה לנציג התחבורה')
  await expect(today.getByRole('button', { name: 'בירור השתתפות מול נציג' })).toHaveCount(0)   // not offered again
  await expect(employee.locator('.copay-banner')).toHaveCount(0)                         // no note under the comparison
  await expect(employee.locator('.summary-row')).toHaveCount(2)                          // the comparison stays
  await shot(employee, '04-record-today')
})

test('3. resend before verification says the link is still valid; the fake verification link resumes the chat', async ({ browser }) => {
  const resend = employee.getByRole('button', { name: 'שליחת קישור אימות מחדש' })
  await expect(resend).toBeVisible()
  await resend.click()
  await expect(employee.locator(RECORD)).toContainText('הקישור הקודם עדיין בתוקף')
  const verify = (await mails()).filter((m) => m.to === EMAIL && m.subject.includes('אימות'))
  expect(verify).toHaveLength(1)
  const other = await newPage(browser, { baseURL: BASE })            // another device, closed when the test ends
  await other.goto(`/employee#return_token=${token(verify[0].body)}`)
  await other.getByRole('button', { name: 'פתיחת השיחה המאובטחת' }).click()
  await expect(other.locator(RECORD)).toHaveCount(1, { timeout: 30_000 })
  await employee.reload()
  await expect(employee.getByRole('button', { name: 'שליחת קישור אימות מחדש' })).toHaveCount(0)   // verified now
})

test.skip('4. NIRO from its card, then the same request typed in the chat: still one record per inquiry', async () => {
  await row(employee, 'NIRO').getByRole('button', { name: 'בירור אישי מול הנציג' }).click()
  const card = employee.locator('.rep-inquiry-card')
  await expect(card.getByLabel('כתובת אימייל לעדכונים')).toHaveCount(0)        // verified: not asked again
  await card.getByRole('button', { name: 'המשך' }).click()
  await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
  await expect(employee.locator(RECORD)).toHaveCount(2)
  await send(employee, 'אני רוצה שנציג התחבורה יאשר את ההשתתפות ב-NIRO')
  await expect(employee.locator(RECORD)).toHaveCount(2)                         // the open question is reused
})

test('5. a question in the chat: offered with its own words, one record', async () => {
  await send(employee, 'יש גג נפתח ב-NIRO?')
  await employee.getByRole('button', { name: /לשאול את נציג הרכב/ }).click()
  const card = employee.locator('.rep-inquiry-card')
  await expect(card.getByLabel('מה תרצו לשאול?')).toHaveValue('יש גג נפתח ב-NIRO?')
  await card.getByRole('button', { name: 'המשך' }).click()
  await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
  await expect(employee.locator(RECORD)).toHaveCount(2)
  await expect(employee.getByRole('button', { name: /לשאול את נציג הרכב/ })).toHaveCount(0)   // not offered again
  await shot(employee, '05-three-records')
})

test.skip('6. the representative: a multi-line general answer, one-click approval, and another amount', async ({ browser }) => {
  const rep = await repScreen(browser, BASE)
  // a shared server (CI runs every spec on one) may hold other employees' open questions: this conversation's are
  // the newest of each kind, and each one answered leaves the open list (one card fewer of its kind)
  const generals = rep.locator('.rep-card:has(.answer-form)', { hasText: 'יש גג נפתח ב-NIRO?' })
  const niros = rep.locator('.rep-card:has(.answer-form)', { hasText: 'מה ההשתתפות החודשית של העובד ב-NIRO' })
  const dolphins = rep.locator('.rep-card:has(.answer-form)',
                               { hasText: 'מה ההשתתפות החודשית של העובד ב-BYD DOLPHIN' })
  const general = generals.last()
  const niro = niros.last()
  const dolphin = dolphins.last()
  await expect(general).toBeVisible({ timeout: 20_000 })
  await expect(niro).toBeVisible()
  await expect(dolphin).toBeVisible()
  const before = [await generals.count(), await niros.count(), await dolphins.count()]
  await expect(niro.getByLabel('הערה לעובד (לא חובה)')).toBeVisible()
  await expect(general.getByLabel('הערה לעובד (לא חובה)')).toHaveCount(0)
  await expect(rep.getByText('ההשתתפות חלה מתאריך')).toHaveCount(0)
  // the employee's own question starts on its own line under the structured text (7B)
  await expect(niro.locator('.rep-question')).toHaveCSS('white-space', 'pre-line')
  const lines = await niro.locator('.rep-question').evaluate((e) => e.getClientRects().length > 0
    && (e.textContent ?? '').includes('\nשאלת העובד:'))
  expect(lines).toBe(true)
  await shot(rep, '06-rep-open-questions')
  await general.getByLabel('תשובה').fill(GENERAL_ANSWER)
  await general.getByRole('button', { name: 'שליחת תשובה לעובד' }).click()
  await expect(generals).toHaveCount(before[0] - 1, { timeout: 20_000 })
  await niro.getByRole('button', { name: /אישור סכום ברירת המחדל/ }).click()       // one click, no note
  await expect(niros).toHaveCount(before[1] - 1, { timeout: 20_000 })
  await dolphin.getByRole('button', { name: 'סכום אחר' }).click()
  await dolphin.locator('input[inputmode="decimal"]').fill('320')
  await dolphin.getByRole('button', { name: 'אישור סכום ושליחת תשובה' }).click()
  await expect(dolphins).toHaveCount(before[2] - 1, { timeout: 20_000 })
  await rep.getByRole('tab', { name: /נענו/ }).click().catch(() => undefined)
  // the multi-line answer in its own full-width block, at 375px too (7B)
  await rep.setViewportSize({ width: 375, height: 900 })
  const value = rep.locator('.rep-card .answered-value', { hasText: 'התוספת מטופלת מול היבואן' }).first()
  await expect(value).toContainText('התוספת מטופלת מול היבואן')
  const [valueW, cardW] = await value.evaluate((e) => [e.getBoundingClientRect().width,
    (e.closest('.rep-card') as HTMLElement).getBoundingClientRect().width])
  expect(valueW / cardW, 'answer block width share').toBeGreaterThan(0.75)
  expect(await rep.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth))
    .toBeLessThanOrEqual(1)
  await shot(rep, '07-rep-answered-375')
})

test.skip('7. the employee: the answer once with its lines, only the right car changed, kept after a refresh', async () => {
  const answer = employee.locator(BOT, { hasText: 'התוספת מטופלת מול היבואן' })
  await expect(answer).toHaveCount(1, { timeout: 30_000 })
  await expect(answer).toContainText('כן, יש גרסה עם גג נפתח.')
  for (let pass = 0; pass < 2; pass++) {
    await expect(employee.locator(BOT, { hasText: 'התוספת מטופלת מול היבואן' })).toHaveCount(1, { timeout: 30_000 })
    await expect(row(employee, 'DOLPHIN')).toContainText('320', { timeout: 30_000 })
    await expect(row(employee, 'DOLPHIN')).toContainText('אושר על ידי הנציג')
    await expect(row(employee, 'NIRO')).toContainText('אושר על ידי הנציג')
    await expect(row(employee, 'NIRO')).toContainText('250')
    await expect(employee.locator(RECORD)).toHaveCount(3)
    await expect(employee.locator(RECORD, { hasText: 'נענתה' })).toHaveCount(3)
    if (pass === 0) await employee.reload()
  }
  await noOverflow(employee)
  await shot(employee, '08-answered-desktop')
  await employee.setViewportSize({ width: 375, height: 812 })
  await noOverflow(employee)
  await shot(employee, '09-answered-375')
})

test.skip('8. the answer notice: one per answer, its link opens the conversation on another device', async ({ browser }) => {
  const notices = (await mails()).filter((m) => m.to === EMAIL && !m.subject.includes('אימות'))
  expect(notices).toHaveLength(3)
  const other = await newPage(browser, { baseURL: BASE })            // another device, closed when the test ends
  await other.goto(`/employee#return_token=${token(notices[0].body)}`)
  await other.getByRole('button', { name: 'פתיחת השיחה המאובטחת' }).click()
  await expect(other.locator(BOT, { hasText: 'התוספת מטופלת מול היבואן' })).toHaveCount(1, { timeout: 30_000 })
  expect(notices.every((m) => !m.body.includes('גג נפתח')), 'no answer content in the email').toBe(true)
})

test.skip('9. mail off: the inquiry and the answer work, and nothing mentions email', async ({ browser }) => {
  const page = await open(browser, OFF, 375)
  for (const m of [PROFILE, CAR, 'NIRO']) await send(page, m)
  await expect(page.locator('.result-card')).toBeVisible({ timeout: 30_000 })
  await page.getByRole('button', { name: /בירור אישי מול הנציג/ }).first().click()
  const card = page.locator('.rep-inquiry-card')
  await expect(card.getByLabel('כתובת אימייל לעדכונים')).toHaveCount(0)
  await expect(card).not.toContainText('אימייל')
  await card.getByRole('button', { name: 'המשך' }).click()
  await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
  await expect(page.locator(RECORD)).toHaveCount(1)
  await expect(page.locator(RECORD)).not.toContainText('אימייל')
  await shot(page, '10-mail-off-record-375')
  const rep = await repScreen(browser, OFF)
  await rep.locator('.rep-card').first().getByRole('button', { name: /אישור סכום ברירת המחדל/ }).click()
  await expect(page.locator(BOT, { hasText: 'נציג התחבורה' })).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.result-card')).toContainText('אושר על ידי הנציג')
  await noOverflow(page)
  await shot(page, '11-mail-off-answered-375')
})
