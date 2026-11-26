// Findings #7 and #8 (24/11), in the browser against an isolated server (templates only, no Gemini):
// #8: a tall new reply (the results of "הכי משתלם") is shown from its TOP, the best car first, not the bottom; a short
// reply keeps the end of the chat in view. The screen is about 10% more compact, but body text stays at least 14px and
// the buttons keep their tap size. #7: the tab icon is the car (svg, ico, apple-touch), not Vite's.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const PAY = { base_salary: 18500, commissions: 0, bonus: 0, pension_percent: 6, study_fund_percent: 2.5,
              credit_points: 2.75, children_under_23: 2 }

async function waitBot(page: Page, bots: number) {
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
}

async function send(page: Page, text: string) {
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await expect(page.getByLabel('שליחה')).toBeEnabled({ timeout: 45_000 })
  await page.getByLabel('שליחה').click()
  await waitBot(page, bots)
}

/** Where the latest bot reply stands in the chat's visible area (px from the chat's top edge). */
async function latestReply(page: Page) {
  return page.evaluate(() => {
    const body = document.querySelector('.chat-body') as HTMLElement
    const last = [...document.querySelectorAll('.message-group.from-bot')].pop() as HTMLElement
    const b = body.getBoundingClientRect()
    const m = last.getBoundingClientRect()
    return { top: m.top - b.top, bottom: m.bottom - b.top, visible: b.height }
  })
}

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })

    test('a tall reply is shown from its top; a short one keeps the end in view', async ({ page }) => {
      await page.goto('/employee')
      await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
      await send(page, 'קבוצה ג')
      await expect.poll(async () => {                                   // a short reply: its end is in view
        const s = await latestReply(page)
        return s.bottom <= s.visible + 2
      }, { timeout: 10_000 }).toBe(true)
      const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
      await page.request.post(`/api/conversations/${id}/messages`, { data: { fields: PAY, source: 'payslip' } })
      await page.reload()
      await expect(page.locator(BOT).last()).toBeVisible({ timeout: 30_000 })
      await send(page, 'יש לי קורולה קרוס 2025 ואני משלמת 300')
      await send(page, 'הכי משתלם')
      await expect(page.locator('.message-group.from-bot').last().locator('.result-card, .summary-card').first())
        .toBeAttached()
      await expect.poll(async () => {                                   // its top, not its bottom (the scroll settles)
        const s = await latestReply(page)
        return s.top >= -2 && s.top < 40
      }, { timeout: 10_000 }).toBe(true)
      const r = await latestReply(page)
      expect(r.bottom).toBeGreaterThan(r.visible)                       // a tall reply: the rest is below
    })

    test('compact, yet readable and easy to tap', async ({ page }) => {
      await page.goto('/employee')
      await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
      const sizes = await page.evaluate(() => {
        const fs = (sel: string) => parseFloat(getComputedStyle(document.querySelector(sel) as Element).fontSize)
        return { bot: fs('.bubble-bot .bubble-text'), composer: fs('textarea'),
                 send: (document.querySelector('.send') as HTMLElement).getBoundingClientRect().height }
      })
      expect(sizes.bot).toBeGreaterThanOrEqual(14)
      expect(sizes.bot).toBeLessThan(width > 720 ? 17 : 16)              // about 10% smaller than before
      expect(sizes.composer).toBeGreaterThanOrEqual(14)
      expect(sizes.send).toBeGreaterThanOrEqual(40)
      for (const chip of await page.locator('.chip:visible').all()) {
        expect((await chip.boundingBox())?.height ?? 0).toBeGreaterThanOrEqual(32)
      }
    })
  })
}

test('the tab icon is the car: svg, ico and apple-touch-icon', async ({ page, request }) => {
  await page.goto('/employee')
  await expect(page).toHaveTitle('יועץ הרכב')
  const icons = await page.locator('link[rel="icon"], link[rel="apple-touch-icon"]').evaluateAll(
    (links) => links.map((l) => l.getAttribute('href')))
  expect(icons).toEqual(['/favicon.ico', '/favicon.svg', '/apple-touch-icon.png'])
  const svg = await (await request.get('/favicon.svg')).text()
  expect(svg).toContain('#0b72a8')
  expect(svg).not.toContain('#863bff')                                   // not Vite's lightning
  for (const [path, type] of [['/favicon.ico', 'image/'], ['/apple-touch-icon.png', 'image/png']]) {
    const res = await request.get(path)
    expect(res.status()).toBe(200)
    expect(res.headers()['content-type']).toContain(type)
  }
})
