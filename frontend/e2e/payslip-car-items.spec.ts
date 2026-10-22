import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

// QA 22/10, ten synthetic payslips through live Gemini: the employee's car copay came back as an "addition to
// pay" on 7 of 10, and the review offered "לשייך לבונוס" for it when the bonus row was empty. Through the real
// server path with a recorded answer (PAYSLIP_FAKE_VISION_DIR, no Gemini): the copay is its own information
// item and can never be assigned to pay, a real addition still can, and a doubtful use value is marked.

const __dirname = dirname(fileURLToPath(import.meta.url))
const PAYSLIPS = join(__dirname, 'fixtures', 'payslips')
const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'

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

async function state(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const r = await page.request.get(`/api/conversations/${id}`)
  expect(r.status()).toBe(200)
  return r.json()
}

async function payslipEnabled(request: APIRequestContext) {
  return (await (await request.get('/api/health')).json()).payslip === true
}

/** Opens a conversation (base salary 15,000 said in chat, nothing else) and the review of car_copay_payslip.pdf. */
async function reviewCopayPayslip(page: Page) {
  await open(page)
  await send(page, 'מנהלת מוצר, שכר בסיס 15,000')
  await page.getByRole('button', { name: 'להעלות תלוש' }).click()
  await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'car_copay_payslip.pdf'))
  await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
}

const row = (page: Page, field: string) => page.locator(`.payslip-row[data-field="${field}"]`)
const info = (page: Page, key: string) => page.locator(`.payslip-info li[data-info="${key}"]`)

test.describe('car items on a payslip are never pay', () => {
  test.beforeEach(async ({ request }) => {
    test.skip(!(await payslipEnabled(request)), 'payslip extraction is off on this server')
  })

  test('a car copay is shown as its own item, with no way to assign it to commissions or bonus', async ({ page }) => {
    await reviewCopayPayslip(page)
    // the payslip has no commissions or bonus rows: both are empty, the case where assigning was offered before
    for (const f of ['commissions', 'bonus']) await expect(row(page, f).locator('input:not([type])')).toHaveValue('')
    const copay = info(page, 'car_copay')
    await expect(copay).toContainText('השתתפות עובד ברכב')
    await expect(copay).toContainText('300')
    await expect(copay).toContainText('ניכוי מהנטו ולא תוספת לשכר')
    await expect(copay.getByRole('button')).toHaveCount(0)
    await expect(page.getByRole('button', { name: /לשייך/ })).toHaveCount(2)      // only the real addition's two
    await expect(info(page, 'other_addition').getByRole('button')).toHaveCount(2)
  })

  test('a real addition is still assigned to bonus, and the copay never reaches the profile', async ({ page }) => {
    await reviewCopayPayslip(page)
    await info(page, 'other_addition').getByRole('button', { name: 'לשייך לבונוס' }).click()
    await expect(row(page, 'bonus').locator('input:not([type])')).toHaveValue('1000')
    await expect(row(page, 'bonus')).toContainText('שויך על ידיכם')
    await expect(info(page, 'car_copay').getByRole('button')).toHaveCount(0)        // still none, commissions empty
    const bots = await page.locator(BOT).count()
    await page.getByRole('button', { name: /אישור ושמירה/ }).click()
    await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
    await expect.poll(async () => (await state(page)).profile.bonus).toBe('1000')
    const p = (await state(page)).profile
    expect([p.base, p.commissions, p.bonus]).toEqual(['15000', null, '1000'])
    expect(Object.values(p).map(String)).not.toContain('300')
  })

  for (const viewport of [{ width: 1366, height: 860 }, { width: 375, height: 800 }]) {
    test(`a doubtful use value is marked for checking (${viewport.width}px)`, async ({ browser }) => {
      const page = await (await browser.newContext({ viewport })).newPage()
      await reviewCopayPayslip(page)
      const use = info(page, 'car_use_value')
      await use.scrollIntoViewIfNeeded()
      await expect(use).toHaveClass(/is-suspect/)
      await expect(use.locator('.tag')).toHaveText('לבדיקה')
      await expect(use.locator('bdi')).toHaveText('3.4 ₪')                       // as read, never rounded to 3
      await expect(use).toContainText('לא נראה סביר')
      await expect(use.getByRole('note')).toBeVisible()
      // the whole item fits the screen: no horizontal page scroll
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
      const box = await use.boundingBox()
      expect(box && box.x >= 0 && box.x + box.width <= viewport.width).toBeTruthy()
      await page.screenshot({ path: test.info().outputPath(`suspect-${viewport.width}.png`) })
      await page.context().close()
    })
  }
})

/** The review of unclear_labels_payslip.pdf: bonus "השתתפות" 300, commissions with no label 500, an addition
 *  "השתתפות" 200, a clear addition 1,000 and a car copay 250 (base salary 15,000 said in chat). */
async function reviewUnclearPayslip(page: Page) {
  await open(page)
  await send(page, 'מנהלת מוצר, שכר בסיס 15,000')
  await page.getByRole('button', { name: 'להעלות תלוש' }).click()
  await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'unclear_labels_payslip.pdf'))
  await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
}

async function confirm(page: Page) {
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name: /אישור ושמירה/ }).click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

// the row's "update" choice: the payslip's value, or (a row with no payslip value) what the employee types (QA 22/10)
const keep = (page: Page, field: string) =>
  row(page, field).locator('input[type="radio"][value="payslip"], input[type="radio"][value="manual"]').first()

test.describe('unclear labels are never saved as pay without a decision', () => {
  test.beforeEach(async ({ request }) => {
    test.skip(!(await payslipEnabled(request)), 'payslip extraction is off on this server')
  })

  test('a bonus "השתתפות" and unlabelled commissions are shown, explained and not preselected', async ({ page }) => {
    await reviewUnclearPayslip(page)
    for (const [f, v, name] of [['bonus', '300 ₪', 'בונוס'], ['commissions', '500 ₪', 'עמלות']]) {
      // the payslip's value is offered, not chosen: the field shows what stays (nothing), never an unsaved amount
      await expect(row(page, f).getByRole('radio', { name: `לעדכן: ${v}` })).not.toBeChecked()
      await expect(row(page, f).locator('input:not([type])')).toHaveValue('')
      await expect(row(page, f)).toContainText(`לא מראה בבירור שזו שורת ${name}`)
      await expect(keep(page, f)).not.toBeChecked()
    }
    // nothing is preselected (the base salary equals the chat's): a blanket confirm saves nothing at all
    await expect(page.getByRole('button', { name: 'אישור ושמירה', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'אישור ושמירה', exact: true }).click()
    await expect(page.getByRole('alert')).toContainText('לא נבחר אף שדה לעדכון')
    const p = (await state(page)).profile
    expect([p.base, p.commissions, p.bonus]).toEqual(['15000', null, null])
  })

  test('the employee can still decide that the unclear line is a bonus', async ({ page }) => {
    await reviewUnclearPayslip(page)
    await keep(page, 'bonus').check()
    await confirm(page)
    await expect.poll(async () => (await state(page)).profile.bonus).toBe('300')
    expect((await state(page)).profile.commissions).toBeNull()
  })

  test('an addition "השתתפות" is marked for clarification, not discarded; the car copay and a clear addition keep their behaviour', async ({ page }) => {
    await reviewUnclearPayslip(page)
    const additions = page.locator('.payslip-info li[data-info="other_addition"]')
    const unclear = additions.filter({ hasText: '200' })
    await expect(unclear.locator('.tag')).toHaveText('לבירור')
    await expect(unclear).toContainText('לא ברור מהתלוש אם זו תוספת לשכר או ניכוי')
    const clear = additions.filter({ hasText: 'תוספת חודשית' })
    await expect(clear.locator('.tag')).toHaveCount(0)
    const copay = info(page, 'car_copay')
    await expect(copay).toContainText('השתתפות עובד ברכב')
    await expect(copay.getByRole('button')).toHaveCount(0)
    await expect(copay.locator('.tag')).toHaveCount(0)
  })

  test('with empty rows, an unclear addition can be assigned on purpose and the copay still cannot', async ({ page }) => {
    await reviewUnclearPayslip(page)
    // the bonus row is "לא לעדכן" and shows nothing: it is empty, so an addition may be assigned into it
    const unclear = page.locator('.payslip-info li[data-info="other_addition"]').filter({ hasText: '200' })
    await unclear.getByRole('button', { name: 'לשייך לבונוס' }).click()
    await expect(row(page, 'bonus').locator('input:not([type])')).toHaveValue('200')
    await expect(row(page, 'bonus')).toContainText('שויך על ידיכם')
    await expect(info(page, 'car_copay').getByRole('button')).toHaveCount(0)
  })
})
