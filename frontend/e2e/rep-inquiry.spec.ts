import { expect, test } from '@playwright/test'
import type { Browser, Page } from '@playwright/test'
import { mkdir, readdir, readFile, stat } from 'node:fs/promises'
import { join } from 'node:path'

// The employee-initiated rep-question card, inside the AI conversation (Package A). One conversation: the
// advisor offers to ask the representative when it cannot answer; the card opens pre-filled, the employee
// edits and confirms, and only then is the question saved. A general question gets a free-text answer and
// never changes the calculation; the answer returns to the chat via polling. Requires REP_TOKEN.

const REP_TOKEN = process.env.REP_TOKEN || ''
const EMAIL_FAKE_DIR = process.env.EMAIL_FAKE_DIR || ''
const BOT = '.bubble-bot .bubble-text'
const PROFILE = 'מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'

async function employee(browser: Browser) {
  const page = await (await browser.newContext()).newPage()
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  return page
}

async function send(page: Page, text: string) {
  const n = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(n)
}

async function repScreen(browser: Browser) {
  const context = await browser.newContext()
  const rep = await context.newPage()
  await rep.goto('/rep')
  await rep.getByLabel('קוד גישה').fill(REP_TOKEN)
  await rep.getByRole('button', { name: 'כניסה' }).click()
  return { rep, close: () => context.close() }
}

async function fakeMailFiles(): Promise<string[]> {
  if (!EMAIL_FAKE_DIR) throw new Error('EMAIL_FAKE_DIR is required for the email round trip')
  await mkdir(EMAIL_FAKE_DIR, { recursive: true })
  return (await readdir(EMAIL_FAKE_DIR)).filter((name) => name.endsWith('.json'))
}

async function fakeEmailAfter(existing: Set<string>) {
  if (!EMAIL_FAKE_DIR) throw new Error('EMAIL_FAKE_DIR is required for the email round trip')
  await expect.poll(async () => (await fakeMailFiles()).some((name) => !existing.has(name))).toBe(true)
  const files = (await fakeMailFiles()).filter((name) => !existing.has(name))
  const newest = (await Promise.all(files.map(async (name) => ({
    name, modified: (await stat(join(EMAIL_FAKE_DIR, name))).mtimeMs,
  })))).sort((a, b) => b.modified - a.modified)[0]
  return JSON.parse(await readFile(join(EMAIL_FAKE_DIR, newest.name), 'utf8')) as {
    to: string; subject: string; body: string; html: string
  }
}

test.describe('rep inquiry card in the conversation', () => {
  test.skip(!REP_TOKEN || !EMAIL_FAKE_DIR, 'REP_TOKEN and EMAIL_FAKE_DIR are required')

  // the advisor offers the representative for a concrete question it cannot answer, once a car is on screen.
  // Naming an alternative car compares it (results appear); then a sunroof question the FAQ cannot answer.
  async function toOfferAfterResults(page: Page) {
    for (const m of [PROFILE, 'יש לי קורולה קרוס, משלמת 300', 'אני רוצה לבדוק NIRO']) await send(page, m)
    await expect(page.locator('.result-card, .summary-card').first()).toBeVisible({ timeout: 30_000 })
    await send(page, 'האם יש לרכב הזה גג נפתח?')
  }

  test('a question the advisor cannot answer offers the rep, the card opens pre-filled, cancel sends nothing', async ({ browser }) => {
    const page = await employee(browser)
    const inquiryRequests: string[] = []
    page.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/rep-question')) {
        inquiryRequests.push(request.url())
      }
    })
    await toOfferAfterResults(page)
    const offer = page.getByRole('button', { name: /לשאול את נציג הרכב/ })
    await expect(offer).toBeVisible({ timeout: 20_000 })
    await offer.click()
    // the card opens, pre-filled with the question the employee asked
    const card = page.locator('.rep-inquiry-card')
    await expect(card).toBeVisible()
    await expect(card.getByLabel('מה תרצו לשאול?')).toHaveValue(/גג נפתח/)
    await expect(card.getByLabel('כתובת אימייל לעדכונים')).toHaveAttribute('required', '')
    // cancel: nothing is created
    await card.getByRole('button', { name: 'ביטול' }).click()
    await expect(page.locator('.rep-inquiry-card')).toHaveCount(0)
    expect(inquiryRequests).toHaveLength(0)
  })

  test('confirm creates one question, the rep sees the exact wording and answers, the reply returns to the chat', async ({ browser }) => {
    const page = await employee(browser)
    const browserErrors: string[] = []
    const watchErrors = (watched: Page) => {
      watched.on('pageerror', (error) => browserErrors.push(error.message))
      watched.on('console', (message) => {
        if (message.type() === 'error') browserErrors.push(message.text())
      })
      watched.on('response', (response) => {
        if (response.url().includes('/api/') && response.status() >= 400) {
          browserErrors.push(`API ${response.status()} ${response.url()}`)
        }
      })
      watched.on('requestfailed', (request) => {
        if (request.url().includes('/api/')) {
          browserErrors.push(`API request failed ${request.url()}: ${request.failure()?.errorText ?? 'unknown error'}`)
        }
      })
    }
    watchErrors(page)
    await toOfferAfterResults(page)
    await page.getByRole('button', { name: /לשאול את נציג הרכב/ }).click()
    const card = page.locator('.rep-inquiry-card')
    const edited = 'האם הרכב קיים עם גג נפתח, ובאיזו עלות?'
    const emailsBeforeVerification = new Set(await fakeMailFiles())
    await card.getByLabel('מה תרצו לשאול?').fill(edited)
    await card.getByLabel('כתובת אימייל לעדכונים').fill('e2e.employee@example.test')
    await card.getByRole('button', { name: 'המשך' }).click()
    await expect(card).toContainText('סיכום לפני שליחה')
    await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
    const history = page.getByTestId('rep-inquiry-history')
    await expect(history).toBeVisible({ timeout: 20_000 })
    await expect(history).toContainText('ממתינה לתשובת הנציג')
    await expect(history).toContainText('נפתחה')
    const verificationMail = await fakeEmailAfter(emailsBeforeVerification)
    expect(verificationMail.to).toBe('e2e.employee@example.test')
    expect(verificationMail.subject).toContain('אימות')
    expect(verificationMail.html).toContain('פתיחת השיחה המאובטחת')
    const verificationToken = verificationMail.body.match(/return_token=([A-Za-z0-9_-]+)/)?.[1]
    expect(verificationToken).toBeTruthy()

    // A second device proves the mailbox and gets a fresh session cookie for this exact conversation.
    const returnContext = await browser.newContext()
    const returnedEmployee = await returnContext.newPage()
    watchErrors(returnedEmployee)
    const returnRequests: string[] = []
    returnedEmployee.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/api/conversations/return')) {
        returnRequests.push(request.url())
      }
    })
    await returnedEmployee.goto(`/employee#return_token=${verificationToken}`)
    const openSecureLink = returnedEmployee.getByRole('button', { name: 'פתיחת השיחה המאובטחת' })
    await expect(openSecureLink).toBeVisible()
    await expect(returnedEmployee.locator(BOT)).toHaveCount(0)
    expect(returnRequests).toHaveLength(0) // an email preview does not redeem the link
    await openSecureLink.click()
    await expect.poll(() => returnRequests.length).toBe(1)
    await expect(returnedEmployee.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
    await expect(returnedEmployee.locator(BOT, { hasText: edited })).toBeVisible()

    // the rep sees the exact question and answers it with free text (general → no copay fields)
    const { rep, close } = await repScreen(browser)
    watchErrors(rep)
    const q = rep.locator('.rep-card', { hasText: 'גג נפתח' }).last()
    await expect(q).toBeVisible({ timeout: 20_000 })
    await expect(q).toContainText('נפתחה:')
    await expect(q).toContainText('ממתינה')
    await expect(q.getByLabel('ההשתתפות חלה מתאריך')).toHaveCount(0)   // no copay date field on a general question
    await q.getByLabel('תשובה').fill('כן, הרכב זמין עם גג נפתח בתוספת תשלום.')

    const emailsBeforeAnswer = new Set(await fakeMailFiles())
    const answerMailPromise = fakeEmailAfter(emailsBeforeAnswer)
    await q.getByRole('button', { name: 'שליחת תשובה לעובד' }).click()
    await close()

    const answerMail = await answerMailPromise
    expect(answerMail.body).not.toContain('כן, הרכב זמין עם גג נפתח בתוספת תשלום.')
    expect(answerMail.to).toBe('e2e.employee@example.test')
    expect(answerMail.html).toContain('פתיחת השיחה המאובטחת')
    const answerToken = answerMail.body.match(/return_token=([A-Za-z0-9_-]+)/)?.[1]
    expect(answerToken).toBeTruthy()

    // The secure, one-time answer link resumes the same chat on the other device, including the rep's answer.
    await returnContext.close()
    const answerContext = await browser.newContext()
    const answeredEmployee = await answerContext.newPage()
    watchErrors(answeredEmployee)
    const answerReturnRequests: string[] = []
    answeredEmployee.on('request', (request) => {
      if (request.method() === 'POST' && request.url().includes('/api/conversations/return')) {
        answerReturnRequests.push(request.url())
      }
    })
    await answeredEmployee.goto(`/employee#return_token=${answerToken}`)
    const openAnswerLink = answeredEmployee.getByRole('button', { name: 'פתיחת השיחה המאובטחת' })
    await expect(openAnswerLink).toBeVisible()
    expect(answerReturnRequests).toHaveLength(0)
    await openAnswerLink.click()
    await expect.poll(() => answerReturnRequests.length).toBe(1)
    const answerText = 'כן, הרכב זמין עם גג נפתח בתוספת תשלום.'
    const answerBubble = answeredEmployee.locator(BOT, { hasText: answerText })
    await expect(answerBubble).toHaveCount(1, { timeout: 30_000 })
    await expect(answeredEmployee.getByTestId('rep-inquiry-history')).toHaveCount(1)
    await expect(answeredEmployee.getByTestId('rep-inquiry-history').first()).toContainText('נענתה')

    // Repeated polling, reload, and a new page in the same browser session must not duplicate the answer.
    await answeredEmployee.waitForTimeout(6500)
    await expect(answerBubble).toHaveCount(1)
    await answeredEmployee.reload()
    await expect(answeredEmployee.locator(BOT, { hasText: answerText })).toHaveCount(1, { timeout: 30_000 })
    await expect(answeredEmployee.getByTestId('rep-inquiry-history')).toHaveCount(1)
    const reconnectedEmployee = await answerContext.newPage()
    watchErrors(reconnectedEmployee)
    await reconnectedEmployee.goto('/employee')
    await expect(reconnectedEmployee.locator(BOT, { hasText: answerText })).toHaveCount(1, { timeout: 30_000 })
    await expect(reconnectedEmployee.getByTestId('rep-inquiry-history')).toHaveCount(1)
    expect(browserErrors).toEqual([])
    await reconnectedEmployee.close()
    await answerContext.close()
  })
})
