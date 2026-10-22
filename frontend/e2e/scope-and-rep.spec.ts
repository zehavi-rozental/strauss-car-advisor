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

async function click(page: Page, name: string) {
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name }).click()
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
    await expect(page.getByRole('button', { name: 'לשאול את הנציג' })).toHaveCount(0)
    await page.context().close()
  })
}

test('a question about the service goes to the representative once; asked again, its status is shown', async ({ page, request }) => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')
  const open_ = async () => (await (await request.get('/api/rep/questions?status=OPEN', {
    headers: { 'X-Rep-Token': REP_TOKEN } })).json()) as { question_text: string }[]
  const question = 'אפשר לקבל אטו 3 בצבע אחר?'
  await open(page)
  await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
  await send(page, question)
  await click(page, 'לשאול את הנציג')
  await click(page, 'לשלוח')
  await expect(page.locator(BOT).last()).toContainText('שלחתי את השאלה לנציג התחבורה')
  const sent = (await open_()).filter((q) => q.question_text.includes('בצבע אחר')).length
  await send(page, question)                                                 // the same question again
  await click(page, 'לשאול את הנציג')
  await expect(page.locator(BOT).last()).toContainText('כבר שאלתי את נציג התחבורה את השאלה הזו')
  await expect(page.getByRole('button', { name: 'לשלוח' })).toHaveCount(0)
  expect((await open_()).filter((q) => q.question_text.includes('בצבע אחר')).length).toBe(sent)
})
