// Help questions and the screen's actions (19/11), in the browser against an isolated server (templates only):
// "להעלות תלוש" is a persistent button from the server's expects.actions while a salary value is missing (the base
// salary included), it opens the existing uploader, it sits where the help text says (under the messages, above the
// message box), and "מעלה תלוש" / "איפה הכפתור?" point to it without losing the question or the data. Synthetic data.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const UPLOAD = '.persistent-actions .link-payslip_upload, .answer-tail .link-button.link-payslip_upload'
const PAY = { base_salary: 18500, commissions: 0, bonus: 0, pension_percent: 6, study_fund_percent: 2.5,
              credit_points: 2.75, children_under_23: 2 }

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

/** The server's real payslip switch (PAYSLIP_ENABLED and a model, or recorded answers): the screen follows it. */
async function payslipOn(page: Page): Promise<boolean> {
  return (await (await page.request.get('/api/health')).json()).payslip === true
}

async function view(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await page.request.get(`/api/conversations/${id}`)).json()
}

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })

    test('the reported conversation: the real upload button, where the reply says, and the question kept', async ({ page }) => {
      test.skip(!(await payslipOn(page)), 'the payslip extraction is off on this server: the next test covers it')
      await page.goto('/employee')
      await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
      await send(page, 'ב')
      const button = page.locator(UPLOAD).first()
      await expect(button).toBeVisible()                                     // at the base salary question
      expect((await view(page)).expects.actions.map((a: { kind: string }) => a.kind)).toEqual(['payslip_upload'])
      for (const text of ['מעלה תלוש', 'איפה הכפתור?', 'אני שואלת איפה הכפתור']) {
        await send(page, text)
        const reply = page.locator(BOT).last()
        await expect(reply).toContainText('מעל תיבת הכתיבה')
        await expect(reply).not.toContainText('לא בטוח שהבנתי')
        await expect(reply).not.toContainText('לא הצלחתי להבין')
        await expect(page.locator(UPLOAD).first()).toBeVisible()
        const v = await view(page)
        expect(v.profile.group).toBe('ב')
        expect(v.profile.base).toBeNull()
        expect(v.stage).toBe('COLLECT_PROFILE')
      }
      // where the words say it is: under the last message, above the message box, and not covered by anything. Polled:
      // the answer area scrolls into view smoothly, so a box read mid-animation is not where the button settles
      const upload = page.locator(UPLOAD).first()
      await upload.scrollIntoViewIfNeeded()
      await expect.poll(async () => {
        const btn = await upload.boundingBox()
        const lastBot = await page.locator(BOT).last().boundingBox()
        const box = await page.getByLabel('הודעה').boundingBox()
        if (!btn || !lastBot || !box) return 'not laid out'
        if (btn.y <= lastBot.y) return 'not under the last message'
        if (btn.y + btn.height > box.y + 1) return 'not above the message box'
        const onTop = await page.evaluate(({ x, y }) => {
          const el = document.elementFromPoint(x, y)
          return Boolean(el && el.closest('.link-payslip_upload'))
        }, { x: btn.x + btn.width / 2, y: btn.y + btn.height / 2 })
        return onTop ? 'ok' : 'covered'
      }, { timeout: 5_000 }).toBe('ok')
      // it opens the existing uploader (no other flow)
      await page.locator(UPLOAD).first().click()
      await expect(page.getByRole('heading', { name: 'העלאת תלוש' })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    })

    test('the extraction off: no button, no upload link, no reply that claims one, the question kept', async ({ page }) => {
      test.skip(await payslipOn(page), 'the payslip extraction is on on this server: the test above covers it')
      await page.goto('/employee')
      await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
      await send(page, 'ב')
      for (const text of ['מעלה תלוש', 'איפה הכפתור?', 'אני רוצה להעלות תלוש']) {
        await send(page, text)
        await expect(page.locator(BOT).last()).not.toContainText('לחצו על "להעלות תלוש')
        await expect(page.locator(BOT).last()).not.toContainText('מעל תיבת הכתיבה')
        await expect(page.locator('.persistent-actions')).toHaveCount(0)
        await expect(page.locator('.link-payslip_upload')).toHaveCount(0)
        const v = await view(page)
        expect(v.expects.actions).toEqual([])
        expect(v.profile.group).toBe('ב')
        expect(v.profile.base).toBeNull()
      }
      await expect(page.locator(BOT).last()).toContainText('לא זמינה')     // the explicit request: said plainly
      await send(page, 'קבוצה ב, שכר בסיס 18,500')                        // Q2: never "או להעלות תלוש"
      await expect(page.locator(BOT).last()).not.toContainText('להעלות תלוש')
      await expect(page.locator('.link-payslip_upload')).toHaveCount(0)
    })

    test('once the salary is complete the button is gone, and no reply claims one', async ({ page }) => {
      await page.goto('/employee')
      await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
      await send(page, 'קבוצה ג')
      const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
      await page.request.post(`/api/conversations/${id}/messages`, { data: { fields: PAY } })
      await page.reload()
      await expect(page.locator(BOT).last()).toBeVisible({ timeout: 30_000 })
      await expect(page.locator('.persistent-actions')).toHaveCount(0)
      await send(page, 'איפה הכפתור?')
      await expect(page.locator(BOT).last()).not.toContainText('להעלות תלוש')
      await expect(page.locator('.persistent-actions')).toHaveCount(0)
      expect((await view(page)).profile.base).toBe('18500')
    })
  })
}
