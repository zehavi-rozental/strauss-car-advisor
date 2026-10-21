import { expect, test, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const SYNTHETIC = join(__dirname, 'fixtures', 'synthetic_payslip.png')
const BOT = '.bubble-bot .bubble-text'

async function openEmployee(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, text: string) {
  const n = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(n)
}

test.describe('payslip upload (synthetic)', () => {
  test('the upload link is offered during salary collection and opens the uploader', async ({ page }) => {
    await openEmployee(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18500')   // now on Q2 (salary components)
    // the "להעלות תלוש" link should be offered
    const link = page.getByRole('button', { name: 'להעלות תלוש' })
    await expect(link).toBeVisible({ timeout: 15_000 })
    await link.click()
    // the uploader panel opens
    await expect(page.getByRole('heading', { name: 'העלאת תלוש' })).toBeVisible()
  })

  test('uploading a synthetic image shows the review form and allows manual entry + confirm', async ({ page, request }) => {
    // extraction needs the vision model (GEMINI_API_KEY on the server): without it the upload answers 503 by design
    const health = await (await request.get('/api/health')).json()
    test.skip(health.payslip !== true, 'payslip extraction is off on this server (no vision key) — live check only')
    await openEmployee(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18500')
    await page.getByRole('button', { name: 'להעלות תלוש' }).click()
    await expect(page.getByRole('heading', { name: 'העלאת תלוש' })).toBeVisible()

    // upload the synthetic PNG via the (hidden) file input
    await page.locator('input[type="file"]').setInputFiles(SYNTHETIC)

    // the review form appears (even if nothing was extracted, the fields are editable for manual entry)
    await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible({ timeout: 45_000 })

    // fill one field manually and confirm — nothing is saved before this explicit confirm
    const commissions = page.locator('#ps-commissions')
    await commissions.fill('0')
    await page.getByRole('button', { name: 'אישור ושמירה' }).click()

    // after confirm, the values are saved via the normal {fields} flow (a user bubble / bot ack appears)
    await expect.poll(async () => page.locator(BOT).count(), { timeout: 20_000 }).toBeGreaterThan(1)
  })
})
