import { expect, test } from '@playwright/test'
import type { Browser, BrowserContext, Page } from '@playwright/test'
import { readdir, readFile } from 'node:fs/promises'
import { join } from 'node:path'

// Mail to the representative's answer, end to end (10/11). Fake mail (files in EMAIL_FAKE_DIR), no model.
// - an inquiry sent by typing in the chat ("לשאול" → "לשלוח") had no address field: its record now offers one
// - the answer email names the question and the answer; its link opens that answer on a phone, highlighted, and a
//   reload keeps it there (#answer=<id>)
// - two employees, two addresses: each mail reaches only its own employee
// - the three car actions share one warm orange (#EC9250) with readable text

const BASE = process.env.E2E_BASE_URL || ''
const REP_TOKEN = process.env.REP_TOKEN || ''
const MAIL = process.env.EMAIL_FAKE_DIR || ''
const BOT = '.bubble-bot .bubble-text'
const RECORD = '[data-testid="rep-inquiry-history"]'
const PROFILE = 'מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const CAR = 'יש לי קורולה 2026, משלמת 280'

test.describe.configure({ mode: 'serial' })
test.skip(!BASE || !REP_TOKEN || !MAIL, 'E2E_BASE_URL, REP_TOKEN, EMAIL_FAKE_DIR')

const contexts: BrowserContext[] = []
test.afterAll(async () => { await Promise.all(contexts.map((c) => c.close().catch(() => undefined))) })

async function newPage(browser: Browser, width = 1280, height = 860): Promise<Page> {
  const context = await browser.newContext({ baseURL: BASE, viewport: { width, height }, hasTouch: width < 600 })
  contexts.push(context)
  return context.newPage()
}

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

async function mailsTo(address: string): Promise<{ to: string; subject: string; body: string; html: string }[]> {
  const files = (await readdir(MAIL)).filter((f) => f.endsWith('.json'))
  const all = await Promise.all(files.map(async (f) => JSON.parse(await readFile(join(MAIL, f), 'utf8'))))
  return all.filter((m) => m.to === address)
}

const token = (body: string) => body.match(/return_token=([A-Za-z0-9_-]+)/)?.[1] ?? ''

async function start(page: Page) {
  await page.goto('/employee')
  await send(page, PROFILE)
  await send(page, CAR)
}

async function answer(browser: Browser, id: string, text: string) {
  const rep = await newPage(browser)
  const res = await rep.request.post(`/api/rep/questions/${id}/answer`, {
    headers: { 'X-Rep-Token': REP_TOKEN }, data: { value: text } })
  expect(res.status()).toBe(200)
}

test('an inquiry sent from the chat offers the address on its record; the answer link opens that answer on a phone, also after a reload', async ({ browser }) => {
  const email = 'dana.hotfix@example.test'
  const page = await newPage(browser)
  await start(page)
  await send(page, 'האם יש ל-MG4 גג נפתח?')
  await send(page, 'לשאול')                                    // typed, not the button: the card is never opened
  await send(page, 'לשלוח')
  const record = page.locator(RECORD).last()
  await expect(record).toContainText('השאלה נשלחה לנציג התחבורה')
  await expect(record).toContainText('ממתינים לתשובת הנציג')
  const field = record.locator('input[type="email"]')
  await expect(field).toBeVisible()                            // the bug: no field when the inquiry came from the chat
  await field.fill('not-an-address')
  await record.getByRole('button', { name: 'שליחת קישור אימות' }).click()
  await expect(record).toContainText('הכתובת לא נראית תקינה')
  await field.fill(email)
  await record.getByRole('button', { name: 'שליחת קישור אימות' }).click()
  await expect(record).toContainText('שלחנו קישור אימות')
  await expect.poll(async () => (await mailsTo(email)).length).toBe(1)
  const [verify] = await mailsTo(email)
  expect(verify.subject).toBe('STRAUSS DRIVE | אימות כתובת האימייל')
  expect((await page.request.post('/api/conversations/return', { data: { token: token(verify.body) } })).status()).toBe(200)

  const id = (await view(page)).rep_questions[0].id as string
  await answer(browser, id, 'לא, ל-MG4 אין גג נפתח.')
  await expect.poll(async () => (await mailsTo(email)).length).toBe(2)
  const notice = (await mailsTo(email)).find((m) => m.subject.includes('תשובה'))!
  expect(notice.body).toContain('האם יש ל-MG4 גג נפתח?')
  expect(notice.body).toContain('לא, ל-MG4 אין גג נפתח.')
  expect(notice.body).toContain("לצפייה בתשובה בצ'אט")

  // the link on a phone, another browser: that conversation, that answer, highlighted; a reload keeps it in view
  const phone = await newPage(browser, 390, 780)
  await phone.goto(`/employee#return_token=${token(notice.body)}`)
  await phone.getByRole('button', { name: /לשיחה|לפתוח|פתיחת/ }).first().click()
  const target = phone.locator('.message-group.is-focused').first()
  await expect(target).toBeVisible({ timeout: 20_000 })
  await expect(target).toContainText('אין גג נפתח')
  await expect(target).toBeInViewport()
  expect(phone.url()).toContain(`#answer=${id}`)
  await phone.reload()
  const again = phone.locator('.message-group.is-focused').first()
  await expect(again).toContainText('אין גג נפתח', { timeout: 20_000 })
  await expect(again).toBeInViewport()
  const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - innerWidth)
  expect(overflow).toBeLessThanOrEqual(1)                      // no sideways scroll on a phone
})

test('two employees, two addresses: each mail reaches only its own employee', async ({ browser }) => {
  const first = 'first.employee@example.test'
  const second = 'second.employee@example.org'
  const a = await newPage(browser)
  const b = await newPage(browser)
  for (const [page, address, question] of [[a, first, 'האם יש ל-NIRO וו גרירה?'], [b, second, 'האם יש ל-MG4 מצלמת רוורס?']] as const) {
    await start(page)
    await send(page, question)
    await page.getByRole('button', { name: 'לשאול את נציג הרכב' }).click()
    const card = page.locator('.rep-inquiry-card')
    await card.getByLabel('כתובת אימייל לעדכון כשהנציג יענה').fill(address)
    await card.getByRole('button', { name: 'המשך' }).click()
    await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
    await expect.poll(async () => (await mailsTo(address)).length).toBe(1)
    await page.request.post('/api/conversations/return', { data: { token: token((await mailsTo(address))[0].body) } })
  }
  const idA = (await view(a)).rep_questions[0].id as string
  const idB = (await view(b)).rep_questions[0].id as string
  await answer(browser, idA, 'כן, יש וו גרירה.')
  await answer(browser, idB, 'כן, יש מצלמת רוורס.')
  await expect.poll(async () => (await mailsTo(first)).length).toBe(2)
  await expect.poll(async () => (await mailsTo(second)).length).toBe(2)
  const noticeA = (await mailsTo(first)).find((m) => m.subject.includes('תשובה'))!
  const noticeB = (await mailsTo(second)).find((m) => m.subject.includes('תשובה'))!
  expect(noticeA.body).toContain('וו גרירה')
  expect(noticeA.body).not.toContain('מצלמת רוורס')
  expect(noticeB.body).not.toContain('וו גרירה')
  // B's browser cannot read A's conversation by its id
  const idOfA = await a.evaluate(() => localStorage.getItem('sca.conversationId'))
  expect([403, 404]).toContain((await b.request.get(`/api/conversations/${idOfA}`)).status())
})

test('the three car actions share one warm orange with readable text', async ({ browser }) => {
  const page = await newPage(browser)
  await start(page)
  const actions = page.locator('.car-actions-row .car-action')
  await expect(actions).toHaveCount(3)
  for (const i of [0, 1, 2]) {
    const [bg, color] = await actions.nth(i).evaluate((el) => {
      const s = getComputedStyle(el)
      return [s.backgroundColor, s.color]
    })
    expect(bg).toBe('rgb(236, 146, 80)')
    expect(color).toBe('rgb(58, 30, 10)')
  }
})
