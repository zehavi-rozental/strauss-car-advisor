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

// The cloud report (21/10): a failed extraction looked like an empty review form. These tests answer the
// extraction request in the browser (no Gemini, no cost) and check that each outcome reads differently.
const FIELDS = ['commissions', 'bonus', 'pension_percent', 'study_fund_percent', 'credit_points', 'children_under_23']
const field = (name: string, value: string | null) => ({ field: name, label: name, value, kind: 'amount',
  confident: true, in_range: true, note: '' })

async function uploadWithAnswer(page: Page, answer: object) {
  await page.route('**/api/payslip/extract', (route) => route.fulfill({ status: 200, json: answer }))
  await openEmployee(page)
  await send(page, 'מנהלת מוצר, שכר בסיס 18500')
  await page.getByRole('button', { name: 'להעלות תלוש' }).click()
  await page.locator('input[type="file"]').setInputFiles(SYNTHETIC)
}

test.describe('payslip outcomes (mocked extraction)', () => {
  test('a failed extraction says so, offers retry and manual entry, and is not shown as a review', async ({ page }) => {
    await uploadWithAnswer(page, { ok: false, status: 'failed', fields: [],
      message_he: 'לא הצלחנו לקרוא את התלוש כרגע. אפשר לנסות שוב בעוד רגע, או למלא את הנתונים ידנית.' })
    await expect(page.getByRole('heading', { name: 'לא הצלחנו לקרוא את התלוש' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('לא הצלחנו לקרוא את התלוש כרגע')
    await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'לנסות שוב' })).toBeVisible()
    await page.getByRole('button', { name: 'מילוי ידני' }).click()
    await expect(page.getByRole('heading', { name: 'מילוי ידני' })).toBeVisible()
    await expect(page.locator('#ps-commissions')).toHaveValue('')
  })

  test('nothing found names the fields that are read and offers another file', async ({ page }) => {
    await uploadWithAnswer(page, { ok: true, status: 'nothing_found', fields: FIELDS.map((f) => field(f, null)),
      message_he: 'לא מצאנו בתלוש אף אחד מהנתונים שהמערכת קוראת: עמלות, בונוס, אחוז הפרשה לפנסיה, אחוז קרן השתלמות, ' +
        'נקודות זיכוי ומספר ילדים. שכר הבסיס לא נקרא מהתלוש. אפשר למלא ידנית או לנסות קובץ אחר.' })
    await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
    await expect(page.getByRole('status')).toContainText('שכר הבסיס לא נקרא מהתלוש')
    await expect(page.getByRole('button', { name: 'לנסות קובץ אחר' })).toBeVisible()
    for (const f of FIELDS) await expect(page.locator(`#ps-${f}`)).toHaveValue('')
  })

  test('a partial extraction prefills what was found and marks the rest as not on the payslip', async ({ page }) => {
    await uploadWithAnswer(page, { ok: true, status: 'partial',
      fields: FIELDS.map((f) => field(f, f === 'bonus' ? '500' : null)),
      message_he: 'זיהינו חלק מהנתונים. שדה ריק לא נמצא בתלוש: מלאו אותו ידנית או השאירו ריק. התלוש עצמו לא נשמר.' })
    await expect(page.getByText('זיהינו חלק מהנתונים')).toBeVisible()
    await expect(page.locator('#ps-bonus')).toHaveValue('500')
    await expect(page.locator('#ps-commissions')).toHaveValue('')
    await expect(page.getByRole('note').first()).toHaveText('לא נמצא בתלוש')
  })
})
