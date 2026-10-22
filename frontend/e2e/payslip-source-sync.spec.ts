import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

// QA 22/10: in the payslip review the employee chose "מהשיחה: 15,000 ₪" and the field still showed the payslip's
// 18000. The field must always show the value of the source chosen, and that value — and only it — is what is
// saved and what the engine calculates. Checked through the real server (recorded payslip answer, no Gemini):
// the result is compared with a second conversation where the same salary is simply typed.

const __dirname = dirname(fileURLToPath(import.meta.url))
const PAYSLIPS = join(__dirname, 'fixtures', 'payslips')
const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const REST = 'אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const CAR = ['יש לי קורולה קרוס', '300', 'אטו 3']

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

/** A second, API-only conversation with the same messages: what the engine says for those typed values. */
async function typedResult(request: APIRequestContext, messages: string[]) {
  const cid = (await (await request.post('/api/conversations')).json()).conversation_id
  for (const text of messages) {
    const r = await request.post(`/api/conversations/${cid}/messages`, { data: { text } })
    expect(r.status()).toBe(200)
  }
  return (await request.get(`/api/conversations/${cid}`)).json()
}

const deltas = (s: { results: { option_id: string; result: { delta_display: string } }[] }) =>
  s.results.map((r) => [r.option_id, Number(r.result.delta_display)])

const base = (page: Page) => page.locator('.payslip-row[data-field="base_salary"]')
const field = (page: Page) => page.locator('#ps-base_salary')
const radio = (page: Page, name: string | RegExp) => base(page).getByRole('radio', { name })

/** Base salary 15,000 said in chat, then the synthetic payslip (18,000) opened for review. */
async function review(page: Page) {
  await open(page)
  await send(page, 'מנהלת מוצר, שכר בסיס 15,000')
  await page.getByRole('button', { name: 'להעלות תלוש' }).click()
  await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'synthetic_payslip.pdf'))
  await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
}

async function confirm(page: Page) {
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name: /אישור ושמירה/ }).click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

test.describe('the payslip review shows, saves and calculates the same value', () => {
  test.beforeEach(async ({ request }) => {
    test.skip(!(await (await request.get('/api/health')).json()).payslip, 'payslip extraction is off on this server')
  })

  for (const viewport of [{ width: 1366, height: 860 }, { width: 375, height: 800 }]) {
    test(`switching source both ways always shows the chosen value (${viewport.width}px)`, async ({ browser }) => {
      const page = await (await browser.newContext({ viewport })).newPage()
      await review(page)
      await expect(field(page)).toHaveValue('')                                   // undecided: nothing shown as chosen
      await radio(page, 'מהשיחה: 15,000 ₪').check()
      await expect(field(page)).toHaveValue('15000')                              // the screenshot's bug: was 18000
      await radio(page, 'מהתלוש: 18,000 ₪').check()
      await expect(field(page)).toHaveValue('18000')
      await radio(page, 'מהשיחה: 15,000 ₪').check()
      await expect(field(page)).toHaveValue('15000')
      await base(page).evaluate((el) => el.scrollIntoView({ block: 'center' }))
      await base(page).screenshot({ path: test.info().outputPath(`chat-chosen-${viewport.width}.png`) })
      await page.context().close()
    })
  }

  test('a typed value is an explicit source, survives switching, and is what is saved and calculated', async ({ page, request }) => {
    await review(page)
    await radio(page, 'מהתלוש: 18,000 ₪').check()
    await field(page).fill('16500')
    await expect(radio(page, 'ערך שהקלדתם: 16,500 ₪')).toBeChecked()               // never "מהתלוש" for a typed value
    await expect(radio(page, 'מהתלוש: 18,000 ₪')).not.toBeChecked()
    await radio(page, 'מהשיחה: 15,000 ₪').check()
    await expect(field(page)).toHaveValue('15000')
    await radio(page, 'ערך שהקלדתם: 16,500 ₪').check()
    await expect(field(page)).toHaveValue('16500')                              // the typed value was not lost
    await confirm(page)
    await expect.poll(async () => (await state(page)).profile.base).toBe('16500')
    await page.reload()                                                          // after a reload: the same value
    await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
    expect((await state(page)).profile.base).toBe('16500')
    for (const m of [REST, ...CAR]) await send(page, m)
    const typed = await typedResult(request, ['מנהלת מוצר, שכר בסיס 16,500', REST, ...CAR])
    expect(deltas(await state(page)).length).toBeGreaterThan(0)
    expect(deltas(await state(page))).toEqual(deltas(typed))                    // the engine used 16,500
  })

  test('choosing the conversation value keeps it: shown 15,000, saved 15,000, calculated with 15,000', async ({ page, request }) => {
    await review(page)
    await radio(page, 'מהשיחה: 15,000 ₪').check()
    await expect(field(page)).toHaveValue('15000')
    await page.getByRole('button', { name: /אישור ושמירה/ }).click()
    await expect(page.getByRole('alert')).toContainText('לא נבחר אף שדה לעדכון')   // nothing to update
    await page.getByRole('button', { name: 'ביטול' }).click()
    expect((await state(page)).profile.base).toBe('15000')
    for (const m of [REST, ...CAR]) await send(page, m)
    const typed = await typedResult(request, ['מנהלת מוצר, שכר בסיס 15,000', REST, ...CAR])
    expect(deltas(await state(page))).toEqual(deltas(typed))
  })
})
