// "אי לי הפרשות, מה זה?" (manual check 19/11), in the browser against an isolated server (templates only): the
// explanation and one confirmation for both contributions, saved only on "לאשר"; "כתבתי שכלום" the same. Desktop/phone.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'

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

async function profile(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await (await page.request.get(`/api/conversations/${id}`)).json()).profile
}

async function toContributions(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await send(page, 'ב')
  await send(page, '18,500')
  await send(page, 'אין עמלות ואין בונוס')
  await expect(page.locator(BOT).last()).toContainText('אילו הפרשות יורדות לכם')
}

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })

    test('the reported message: explained, both held for one confirmation, saved on "לאשר"', async ({ page }) => {
      await toContributions(page)
      await send(page, 'אי לי הפרשות, מה זה?')
      const reply = page.locator(BOT).last()
      await expect(reply).toContainText('הפרשות הן האחוזים שיורדים לכם מהשכר')
      await expect(reply).toContainText('לרשום פנסיה 0% וקרן השתלמות 0%')
      expect((await profile(page)).pension_percent).toBeNull()                 // nothing before the confirmation
      const confirm = page.getByRole('button', { name: 'לאשר' })
      await expect(confirm).toBeVisible()
      await expect(page.getByRole('button', { name: 'לתקן' })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      const bots = await page.locator(BOT).count()
      await confirm.click()
      await waitBot(page, bots)
      const p = await profile(page)
      expect([Number(p.pension_percent), Number(p.study_fund_percent)]).toEqual([0, 0])
      await expect(page.locator(BOT).last()).not.toContainText('כמה אחוזים מהשכר מופרשים מכם לפנסיה')  // not asked again
    })

    test('"כתבתי שכלום" after the contributions question is the same answer', async ({ page }) => {
      await toContributions(page)
      await send(page, 'כתבתי שכלום')
      await expect(page.locator(BOT).last()).toContainText('לרשום פנסיה 0% וקרן השתלמות 0%')
      await expect(page.locator(BOT).last()).not.toContainText('אחד-אחד')
    })
  })
}
