// UI polish in the hotfix (10/11): the blue band as it was with "שיחה חדשה" in it; under it, with no bar of its own,
// "עריכת הנתונים שלי" on the end side and "ממתין לתשובת הנציג" only while an inquiry is open (and gone when it is
// answered); the three car buttons each in a quiet colour of their own; a new conversation resets only what was on screen.
// Desktop and 375px. Synthetic data only.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const REP_TOKEN = process.env.REP_TOKEN
const PAY = { base_salary: 18000, commissions: 0, bonus: 0, pension_percent: 6, study_fund_percent: 2.5, credit_points: 2.25,
              children_under_23: 2 }

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  return page.evaluate(() => localStorage.getItem('sca.conversationId')) as Promise<string>
}

async function post(page: Page, id: string, data: object) {
  const r = await page.request.post(`/api/conversations/${id}/messages`, { data })
  expect(r.status()).toBe(200)
}

async function toCars(page: Page) {
  const id = await open(page)
  await post(page, id, { text: 'קבוצה ג' })
  await post(page, id, { fields: PAY })
  await post(page, id, { text: 'קורולה 2026' })
  await post(page, id, { choice: 'catalog:CAR-07', field: 'copay' })
  await page.reload()
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  return id
}

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })

    test('the band keeps "שיחה חדשה"; "עריכת הנתונים שלי" sits under it on the end side, inside no bar', async ({ page }) => {
      await toCars(page)
      const header = (await page.locator('.chat-header').boundingBox())!
      const fresh = page.locator('.chat-header').getByRole('button', { name: 'שיחה חדשה' })
      await expect(fresh).toBeVisible()
      await expect(fresh).toContainText('שיחה חדשה')
      const edit = page.getByRole('button', { name: /עריכת הנתונים שלי/ })
      await expect(edit).toHaveCount(1)
      await expect(page.locator('.chat-header').getByRole('button', { name: /עריכת הנתונים שלי/ })).toHaveCount(0)
      const box = (await edit.boundingBox())!
      expect(box.y).toBeGreaterThanOrEqual(header.y + header.height - 1)          // under the blue band
      expect(box.x + box.width / 2).toBeLessThan(width / 2)                        // the end side (left in RTL)
      if (width > 520) await expect(edit).toContainText('עדכון וחישוב מחדש')
      const bar = await page.locator('.chat-tools').evaluate((e) => getComputedStyle(e).backgroundColor)
      expect(bar).toBe('rgba(0, 0, 0, 0)')                                        // no rectangle of its own
      // the tools row is a row of the page: the conversation starts under it, never behind it
      const tools = (await page.locator('.chat-tools').boundingBox())!
      const body = (await page.locator('.chat-body').boundingBox())!
      expect(body.y).toBeGreaterThanOrEqual(tools.y + tools.height - 1)
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      await edit.click()
      await expect(page.getByRole('dialog', { name: 'הנתונים שלי' })).toContainText('18,000 ₪')
    })

    // the design since 10/11 21:15 (styles/data.css): warm orange #EC9250 with deep brown ink #3A1E0A, 6.4:1 (AA);
    // white on it was 2.4:1. This test still asked for the earlier white-on-deep-orange (15/11: stale, not a bug)
    test('the three car buttons: one warm orange with dark readable text, the same corners, a clear focus ring', async ({ page }) => {
      await toCars(page)
      const buttons = page.locator('.car-actions-row .car-action')
      await expect(buttons).toHaveCount(3)
      const looks = await buttons.evaluateAll((els) => els.map((e) => {
        const c = getComputedStyle(e)
        return [c.backgroundColor, c.color, c.borderTopLeftRadius].join('|')
      }))
      expect(new Set(looks)).toEqual(new Set(['rgb(236, 146, 80)|rgb(58, 30, 10)|' + looks[0].split('|')[2]]))
      for (const b of await buttons.all()) {                        // fits the screen, nothing cut off
        const box = (await b.boundingBox())!
        expect(box.x).toBeGreaterThanOrEqual(0)
        expect(box.x + box.width).toBeLessThanOrEqual(width + 1)
      }
      await buttons.first().focus()
      await page.keyboard.press('Tab')
      const ring = await page.evaluate(() => getComputedStyle(document.activeElement!).outlineStyle)
      expect(ring).not.toBe('none')
    })
  })
}

test('"ממתין לתשובת הנציג" shows only while an inquiry is open, and goes when it is answered', async ({ page }) => {
  test.skip(!REP_TOKEN, 'REP_TOKEN')
  const id = await toCars(page)
  await expect(page.locator('.rep-waiting')).toHaveCount(0)
  const asked = await page.request.post(`/api/conversations/${id}/rep-question`,
                                        { data: { question: `האם יש ל-MG4 גג נפתח? (${Date.now()})`, intent: 'general' } })
  expect(asked.status()).toBe(200)
  await page.reload()
  const waiting = page.getByRole('status').filter({ hasText: 'ממתין לתשובת הנציג' })
  await expect(waiting).toBeVisible({ timeout: 30_000 })
  const rep = { 'X-Rep-Token': REP_TOKEN! }
  // this conversation's own question, by the id its sending returned (never by text: another conversation may ask the same)
  const qid = (await asked.json()).question_id as string
  expect(qid).toBeTruthy()
  expect((await page.request.post(`/api/rep/questions/${qid}/answer`, { headers: rep, data: { value: 'כן' } })).status()).toBe(200)
  await expect(page.locator('.rep-waiting')).toHaveCount(0, { timeout: 30_000 })   // the screen polls, and the status follows
})

test('a new conversation resets only what was on screen: the data, the status, the panel', async ({ page }) => {
  const old = await toCars(page)
  await page.getByRole('button', { name: /עריכת הנתונים שלי/ }).click()
  await expect(page.getByRole('dialog', { name: 'הנתונים שלי' })).toBeVisible()
  await page.keyboard.press('Escape')
  page.once('dialog', (d) => void d.accept())
  await page.locator('.chat-header').getByRole('button', { name: 'שיחה חדשה' }).click()
  await expect.poll(async () => page.evaluate(() => localStorage.getItem('sca.conversationId'))).not.toBe(old)
  await expect(page.locator(BOT)).toHaveCount(1, { timeout: 30_000 })              // the greeting only
  await expect(page.getByRole('dialog', { name: 'הנתונים שלי' })).toHaveCount(0)
  await expect(page.locator('.rep-waiting')).toHaveCount(0)
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const fresh = await (await page.request.get(`/api/conversations/${id}`)).json()
  expect(fresh.profile.base).toBeNull()
  await expect(page.getByRole('button', { name: /עריכת הנתונים שלי, \d+ חסרים/ })).toBeVisible()
})
