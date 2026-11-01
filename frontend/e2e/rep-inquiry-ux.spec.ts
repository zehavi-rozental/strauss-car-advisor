import { expect, test } from '@playwright/test'
import type { Browser, BrowserContext, Page } from '@playwright/test'
import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

// The question to the representative, end to end (live QA 1/11): after "האם יש רכב עם גג נפתח?" was sent, the reload
// put the comparison under the inquiry and the page followed it down. Now: the record says it was sent and stays in
// view (also through the polls), the comparison stays at its own message, the answer is one card with the question,
// and the email's link opens that very answer on another device. Fake mail (files in EMAIL_FAKE_DIR), no model.

const BASE = process.env.E2E_BASE_URL || ''
const REP_TOKEN = process.env.REP_TOKEN || ''
const MAIL = process.env.EMAIL_FAKE_DIR || ''
const BOT = '.bubble-bot .bubble-text'
const RECORD = '[data-testid="rep-inquiry-history"]'
const EMAIL = 'ux.employee@example.test'
const PROFILE = 'מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const FIRST = 'האם יש רכב עם גג נפתח?'
const SECOND = 'אפשר לקבל אטו 3 עם וו גרירה?'
const ANSWER = 'כן, ב-NIRO יש גרסה עם גג נפתח.\nהתוספת מטופלת מול היבואן.'

test.describe.configure({ mode: 'serial' })
test.skip(!BASE || !REP_TOKEN || !MAIL, 'E2E_BASE_URL, REP_TOKEN, EMAIL_FAKE_DIR')

const contexts: BrowserContext[] = []
let employee: Page
let mailBefore = new Set<string>()
const ids: string[] = []

async function newPage(browser: Browser, width = 1280): Promise<Page> {
  const context = await browser.newContext({ baseURL: BASE, viewport: { width, height: 860 } })
  contexts.push(context)
  return context.newPage()
}
test.afterAll(async () => { await Promise.all(contexts.map((c) => c.close().catch(() => undefined))) })

async function send(page: Page, text: string) {
  const n = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(n)
}

async function view(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await page.request.get(`/api/conversations/${id}`)).json()
}

async function mails(): Promise<{ to: string; subject: string; body: string; html: string }[]> {
  const files = (await readdir(MAIL)).filter((f) => f.endsWith('.json') && !mailBefore.has(f))
  return Promise.all(files.map(async (f) => JSON.parse(await readFile(join(MAIL, f), 'utf8'))))
}

const token = (body: string) => body.match(/return_token=([A-Za-z0-9_-]+)/)?.[1] ?? ''
const inView = (page: Page, selector: string) => page.locator(selector).first().evaluate((el) => {
  const r = el.getBoundingClientRect()
  return r.top < innerHeight && r.bottom > 0
})
const groupOf = (page: Page, selector: string) =>
  page.locator(selector).first().evaluate((el) => el.closest('.message-group')?.id ?? '')

/** Ask through the advisor's offer: the card opens with the question; returns the new inquiry's id. */
async function ask(page: Page, question: string, email?: string): Promise<string> {
  const before = new Set((await view(page)).rep_questions.map((q: { id: string }) => q.id))
  await send(page, question)
  await page.getByRole('button', { name: 'לשאול את נציג הרכב' }).click()
  const card = page.locator('.rep-inquiry-card')
  await expect(card.getByLabel('מה תרצו לשאול?')).toHaveValue(question)
  if (email) await card.getByLabel('כתובת אימייל לעדכון כשהנציג יענה').fill(email)
  await card.getByRole('button', { name: 'המשך' }).click()
  await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
  await expect(page.locator('.rep-inquiry-card')).toHaveCount(0)
  await expect.poll(async () => (await view(page)).rep_questions.length).toBe(before.size + 1)
  const id = (await view(page)).rep_questions.map((q: { id: string }) => q.id).find((x: string) => !before.has(x))
  return id as string
}

async function answer(browser: Browser, id: string, text: string) {
  const rep = await newPage(browser)
  const res = await rep.request.post(`/api/rep/questions/${id}/answer`, {
    headers: { 'X-Rep-Token': REP_TOKEN }, data: { value: text } })
  expect(res.status()).toBe(200)
}

test('1-3. sent: a clear record in view, the waiting status, and the comparison stays at its own message', async ({ browser }) => {
  mailBefore = new Set((await readdir(MAIL)).filter((f) => f.endsWith('.json')))
  employee = await newPage(browser)
  await employee.goto('/employee')
  await expect(employee.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  for (const m of [PROFILE, 'יש לי קורולה קרוס, משלמת 300', 'NIRO ו-DOLPHIN']) await send(employee, m)
  await expect(employee.locator('.summary-card')).toBeVisible({ timeout: 30_000 })
  const resultsAt = await groupOf(employee, '.result-cards')

  ids.push(await ask(employee, FIRST, EMAIL))
  const record = employee.locator(RECORD).last()
  await expect(record).toContainText('השאלה נשלחה לנציג התחבורה')
  await expect(record).toContainText(FIRST)
  await expect(record).toContainText('ממתינים לתשובת הנציג')
  await expect(record).toContainText('אחרי האימות נעדכן אתכם גם באימייל')   // an address waiting: said honestly
  await expect.poll(async () => inView(employee, `#rep-inquiry-${ids[0]}`)).toBe(true)
  expect(await groupOf(employee, '.result-cards')).toBe(resultsAt)          // never re-drawn under the inquiry
  await expect(employee.locator('.result-cards')).toHaveCount(1)
  await employee.waitForTimeout(7000)                                        // two polls: the page stays put
  expect(await inView(employee, `#rep-inquiry-${ids[0]}`)).toBe(true)
  await send(employee, 'מה זה שווי שימוש?')                                   // the conversation goes on meanwhile
  await expect(employee.locator(BOT).last()).toContainText('שווי')
})

test('4. the answer: one card with the question and the full answer, its lines kept', async ({ browser }) => {
  const verify = (await mails()).find((m) => m.to === EMAIL && m.subject.includes('אימות'))!
  const other = await newPage(browser)                                      // the address is verified by its link
  await other.goto(`/employee#return_token=${token(verify.body)}`)
  await other.getByRole('button', { name: 'פתיחת השיחה המאובטחת' }).click()
  await expect(other.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await answer(browser, ids[0], ANSWER)
  const card = employee.locator('.rep-answer-card')
  await expect(card).toHaveCount(1, { timeout: 30_000 })
  await expect(card).toContainText('הנציג השיב')
  await expect(card).toContainText('השאלה שלכם:')
  await expect(card.locator('.rep-answer-card-question')).toHaveText(FIRST)
  await expect(card).toContainText('כן, ב-NIRO יש גרסה עם גג נפתח.')
  expect(await card.locator('.bubble-text').innerText()).toContain('גג נפתח.\nהתוספת מטופלת מול היבואן.')
  await expect(employee.locator(RECORD).first()).toContainText('נענתה')
})

test('5-7. two inquiries: one notice each, and each link opens its own answer on another device', async ({ browser }) => {
  ids.push(await ask(employee, SECOND))
  await answer(browser, ids[1], 'כן, אפשר להזמין וו גרירה.')
  await expect(employee.locator('.rep-answer-card')).toHaveCount(2, { timeout: 30_000 })
  const notices = (await mails()).filter((m) => m.to === EMAIL && m.subject.includes('תשובה'))
  expect(notices).toHaveLength(2)                                           // one per answer, never doubled
  // each notice names its own question (the files are not in sending order: matched by what they say)
  const byQuestion = [FIRST, SECOND].map((q) => notices.find((n) => n.body.includes(`השאלה שלכם: ${q}`))!)
  expect(byQuestion.every(Boolean)).toBe(true)
  expect(byQuestion[0].html).toContain('לצפייה בתשובת הנציג')
  expect(notices.every((n) => !n.body.includes('התוספת מטופלת') && !n.body.includes('18500'))).toBe(true)
  for (const [i, notice] of byQuestion.entries()) {
    const phone = await newPage(browser, 375)                               // another device, a phone
    await phone.goto(`/employee#return_token=${token(notice.body)}`)
    await phone.getByRole('button', { name: 'פתיחת השיחה המאובטחת' }).click()
    const focused = phone.locator('.message-group.is-focused')
    await expect(focused).toHaveCount(1, { timeout: 30_000 })
    await expect(focused.locator('.rep-answer-card')).toContainText(i === 0 ? FIRST : SECOND)   // the exact answer
    await expect.poll(async () => inView(phone, '.message-group.is-focused')).toBe(true)
    expect(await phone.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(0)
  }
})

test('8. a reload keeps the records and the answers; asked again, nothing is sent twice', async () => {
  await employee.reload()
  await expect(employee.locator(RECORD)).toHaveCount(2, { timeout: 30_000 })
  await expect(employee.locator('.rep-answer-card')).toHaveCount(2)
  const records = (await view(employee)).rep_questions.length
  await send(employee, FIRST)                                                // the same question again
  await expect(employee.getByRole('button', { name: 'לשאול את נציג הרכב' })).toHaveCount(0)
  expect((await view(employee)).rep_questions.length).toBe(records)
})
