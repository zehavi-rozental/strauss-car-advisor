// Two ways to give the salary details (19/11), in the browser against an isolated server (templates only; the payslip
// read from RECORDED answers, no Gemini): answering in the chat, or uploading a payslip, checking and confirming it.
// Both lead to the same conversation; what was confirmed is kept and never asked again; a failed reading says why
// (the service, the file, or a test server) and the chat goes on. Desktop and phone. Synthetic data only.
import { writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const HERE = dirname(fileURLToPath(import.meta.url))
const PAYSLIPS = join(HERE, 'fixtures', 'payslips')
const CHAT = "להמשיך בהזנה בצ'אט"
const UPLOAD = 'להעלות תלוש שכר'
const CHOICE = '.payslip-choice'

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

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

async function state(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await page.request.get(`/api/conversations/${id}`)).json()
}

async function openUploader(page: Page) {
  await page.getByRole('button', { name: UPLOAD }).click()
  await expect(page.getByRole('heading', { name: 'העלאת תלוש שכר' })).toBeVisible()
}

async function upload(page: Page, file: string) {
  await openUploader(page)
  await page.locator('input[type="file"]').setInputFiles(file.includes('/') || file.includes('\\') ? file : join(PAYSLIPS, file))
}

async function confirm(page: Page) {
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name: /אישור ושמירה/ }).click()
  await waitBot(page, bots)
}

const row = (page: Page, field: string) => page.locator(`.payslip-row[data-field="${field}"]`)

const fitsWidth = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)

for (const [width, height] of [[1280, 860], [375, 800]]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height } })
    test.beforeEach(async ({ request }) => {
      test.skip((await (await request.get('/api/health')).json()).payslip !== true,
                'the payslip extraction is off on this server (help-actions spec covers it)')
    })

    test('1. the chat alone: both ways explained once, the chat chosen, every detail typed, no upload needed', async ({ page }) => {
      await open(page)
      const choice = page.locator(CHOICE)
      await expect(choice).toContainText('את פרטי השכר אפשר למלא כאן בשיחה, שאלה אחר שאלה')
      await expect(choice.getByRole('button', { name: CHAT })).toBeVisible()
      await expect(choice.getByRole('button', { name: UPLOAD })).toBeVisible()
      expect(await fitsWidth(page)).toBe(true)
      await choice.getByRole('button', { name: CHAT }).click()
      await expect(page.locator(CHOICE)).toHaveCount(0)                       // explained once, then out of the way
      await expect(page.getByRole('button', { name: UPLOAD })).toBeVisible()  // still there, compact
      await expect(page.getByLabel('הודעה')).toBeFocused()
      await send(page, 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, ' +
                       '2.75 נקודות זיכוי, 2 ילדים')
      const s = await state(page)
      expect(s.stage).toBe('COLLECT_CURRENT_CAR')
      expect([s.profile.base, s.profile.pension_percent, s.profile.children]).toEqual(['18500', '6', 2])
      await expect(page.getByRole('button', { name: UPLOAD })).toHaveCount(0)  // the salary is complete
      await page.reload()
      await expect(page.locator(CHOICE)).toHaveCount(0)
    })

    test('2. choosing the upload: a clear window, what to upload and why, and back to the chat', async ({ page }) => {
      await open(page)
      await page.locator(CHOICE).getByRole('button', { name: UPLOAD }).click()
      const panel = page.locator('.payslip-upload')
      await expect(panel.getByRole('heading', { name: 'העלאת תלוש שכר' })).toBeVisible()
      await expect(panel).toContainText('תלוש השכר האחרון')
      await expect(panel).toContainText('PDF')
      await expect(panel).toContainText('JPG')
      await expect(panel).toContainText('יוצגו לכם לבדיקה ולתיקון')
      expect(await fitsWidth(page)).toBe(true)
      await panel.getByRole('button', { name: CHAT }).click()
      await expect(page.locator('.payslip-upload')).toHaveCount(0)
      await send(page, 'קבוצה ג, שכר בסיס 18,500')
      expect((await state(page)).profile.base).toBe('18500')
    })

    test('3+6. a synthetic payslip: read, corrected, confirmed; the chat goes on without asking it again', async ({ page }) => {
      await open(page)
      await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
      await upload(page, 'full_payslip.pdf')
      await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
      const points = row(page, 'credit_points').locator('input:not([type])')
      await expect(points).toHaveValue('2.75')
      await points.fill('3')                                                 // a correction before confirming
      await confirm(page)
      const s = await state(page)
      expect(Number(s.profile.credit_points)).toBe(3)                        // the corrected value, not the read one
      expect([s.profile.base, s.profile.pension_percent, s.profile.study_fund_percent]).toEqual(['18500', '6', '2.5'])
      expect(s.stage).toBe('COLLECT_CURRENT_CAR')                            // nothing confirmed is asked again
      const next = await page.locator(BOT).last().innerText()
      for (const asked of ['שכר הבסיס', 'נקודות זיכוי', 'קרן השתלמות']) expect(next).not.toContain(`${asked}?`)
      expect(await fitsWidth(page)).toBe(true)
    })

    test('4+5. the chat, then the payslip, then the chat: a value given in the chat is kept when chosen', async ({ page }) => {
      await open(page)
      await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
      await send(page, 'פנסיה 7%')
      await upload(page, 'full_payslip.pdf')
      const pension = row(page, 'pension_percent')
      await expect(pension).toContainText('שונה ממה שכתבתם בשיחה')
      await pension.getByRole('radio', { name: /מהשיחה/ }).check()         // keep the chat's 7%
      await confirm(page)
      let s = await state(page)
      expect(Number(s.profile.pension_percent)).toBe(7)
      expect(s.profile.base).toBe('18500')
      expect(Number(s.profile.study_fund_percent)).toBe(2.5)                 // the rest from the payslip
      await send(page, 'יש לי קורולה קרוס 2025')                             // and back to the chat
      s = await state(page)
      expect(Number(s.profile.pension_percent)).toBe(7)
      expect(s.current_car.label).toContain('COROLLA CROSS')
    })

    test('7. failed readings say why, and the chat goes on with nothing lost', async ({ page }, info) => {
      await open(page)
      await send(page, 'קבוצה ג, שכר בסיס 18,500')
      // a file the test server has no recording for: its own limit, said as such
      const unknown = info.outputPath('my-payslip.pdf')
      writeFileSync(unknown, '%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]' +
        '/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n')
      await upload(page, unknown)
      const panel = page.locator('.payslip-upload')
      await expect(panel.getByRole('heading', { name: /סביבת בדיקות/ })).toBeVisible()
      await expect(panel).toContainText('להמשיך בהזנה בצ\'אט')
      // the reading service is busy: a service problem, not the file
      await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'quota_payslip.pdf'))
      await expect(panel.getByRole('heading', { name: /שירות הקריאה לא זמין/ })).toBeVisible()
      // the file refused: a file problem
      await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'rejected_payslip.pdf'))
      await expect(panel.getByRole('heading', { name: /הקובץ לא התקבל לקריאה/ })).toBeVisible()
      await panel.getByRole('button', { name: CHAT }).click()
      await expect(page.locator('.payslip-upload')).toHaveCount(0)
      await send(page, 'אין עמלות ואין בונוס')
      const s = await state(page)
      expect(s.profile.base).toBe('18500')                                   // nothing lost
      expect([s.profile.commissions, s.profile.bonus]).toEqual(['0', '0'])
      expect(await fitsWidth(page)).toBe(true)
    })
  })
}
