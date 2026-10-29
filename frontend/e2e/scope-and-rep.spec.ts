import { expect, test, type Page } from '@playwright/test'

// QA 22/10, in the browser against the real server without an LLM: an off-topic question gets the redirect and
// no representative buttons; a question about the service still can go to the representative, once.

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const REP_TOKEN = process.env.REP_TOKEN || ''
const REDIRECT = 'אני כאן כדי לעזור לכם להבין איך בחירת רכב החברה משפיעה על הנטו החודשי שלכם'

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, text: string) {
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
  const users = await page.locator(USER).count()
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 45_000 }).toBeGreaterThan(users)
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

for (const viewport of [{ width: 1366, height: 860 }, { width: 375, height: 800 }]) {
  test(`an off-topic question is redirected, with no representative buttons (${viewport.width}px)`, async ({ browser }) => {
    const page = await (await browser.newContext({ viewport })).newPage()
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
    await send(page, 'מה מזג האויר?')                                       // the screenshot, one vav
    await expect(page.locator(BOT).last()).toContainText(REDIRECT)
    await expect(page.locator(BOT).last()).not.toContainText('נציג')
    // no offer to ask the representative, whatever the button says ("לשאול את נציג הרכב" since the inquiry card)
    await expect(page.getByRole('button', { name: /לשאול את (ה)?נציג/ })).toHaveCount(0)
    await page.context().close()
  })
}

test('a question about the service goes to the representative once; asked again, its status is shown', async ({ page, request }) => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')
  const open_ = async () => (await (await request.get('/api/rep/questions?status=OPEN', {
    headers: { 'X-Rep-Token': REP_TOKEN } })).json()) as { question_text: string }[]
  const question = 'אפשר לקבל אטו 3 בצבע אחר?'
  // the offer opens the inquiry card in the chat, pre-filled with the question; sending it is confirmed
  const ask = async () => {
    await page.getByRole('button', { name: 'לשאול את נציג הרכב' }).click()
    const card = page.locator('.rep-inquiry-card')
    await expect(card.getByLabel('מה תרצו לשאול?')).toHaveValue(question)
    const email = card.getByLabel('כתובת אימייל לעדכונים')
    if (await email.count()) await email.fill('scope.employee@example.test')     // mail on: an address is asked
    await card.getByRole('button', { name: 'המשך' }).click()
    await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
  }
  const asked = async () => (await open_()).filter((q) => q.question_text.includes('בצבע אחר')).length
  const base = await asked()                       // another conversation's same question may be open already
  await open(page)
  await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
  await send(page, question)
  await ask()
  const records = page.getByTestId('rep-inquiry-history')
  await expect(records).toHaveCount(1)
  await expect(records.first()).toContainText('ממתינה לתשובת הנציג')
  const sent = await asked()
  expect(sent).toBe(base + 1)                      // sent once
  await send(page, question)                                                 // the same question again
  await ask()
  // the open inquiry is pointed to, nothing is sent again
  await expect(page.locator('.rep-inquiry-card.is-exists')).toContainText('כבר קיימת פנייה פתוחה')
  await expect(page.locator('.rep-inquiry-card.is-exists')).toContainText('לא שלחנו פנייה נוספת')
  await expect(records).toHaveCount(1)
  expect(await asked()).toBe(sent)                 // not sent again
})
