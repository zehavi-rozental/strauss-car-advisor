import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

// End-to-end journeys against the real server: conversation, payslip review, calculation engine and the
// representative. Every journey checks the server's state through the API (same browser session), not only
// the screen. Expected amounts are the engine's reference results (Strauss's six sample employees) or are
// compared with a second conversation that typed the same values.
//
// Payslip journeys need a server started with PAYSLIP_ENABLED=true and PAYSLIP_FAKE_VISION_DIR pointing at
// e2e/fixtures/payslips/vision: the vision model's answers are RECORDED per file (see make_fixtures.py). This
// tests our server, review and conversation with fixed model answers; it is NOT a live Gemini check.

const __dirname = dirname(fileURLToPath(import.meta.url))
const PAYSLIPS = join(__dirname, 'fixtures', 'payslips')
const REP_TOKEN = process.env.REP_TOKEN || ''
const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'

const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const REST_OF_NOA = 'אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

/** Sends one message and waits for its own bubble and then a new reply (a reply still arriving for an
 *  earlier action, such as a payslip confirm, is not mistaken for this message's reply). */
async function send(page: Page, text: string) {
  await expect(page.getByLabel('שליחה')).toBeVisible()
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
  const users = await page.locator(USER).count()
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await expect(page.getByLabel('שליחה')).toBeEnabled({ timeout: 45_000 })
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 45_000 }).toBeGreaterThan(users)
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

/** Confirms the payslip review and waits for the conversation's reply to it. */
async function confirmPayslip(page: Page, name: string | RegExp = /אישור ושמירה/) {
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name }).click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

async function conversationId(page: Page): Promise<string> {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  expect(id).toBeTruthy()
  return id as string
}

/** The server's view of this browser's conversation (same session cookie). */
async function state(page: Page) {
  const r = await page.request.get(`/api/conversations/${await conversationId(page)}`)
  expect(r.status()).toBe(200)
  return r.json()
}

const deltas = (s: { results: { option_id: string; result: { delta_display: string } }[] }) =>
  s.results.map((r) => [r.option_id, Number(r.result.delta_display)])

async function payslipEnabled(request: APIRequestContext) {
  return (await (await request.get('/api/health')).json()).payslip === true
}

async function uploadPayslip(page: Page, file: string) {
  await page.getByRole('button', { name: 'להעלות תלוש' }).click()
  await expect(page.getByRole('heading', { name: 'העלאת תלוש' })).toBeVisible()
  await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, file))
}

const row = (page: Page, field: string) => page.locator(`.payslip-row[data-field="${field}"]`)
const keep = (page: Page, field: string) => row(page, field).getByRole('checkbox')

/** A second, API-only conversation with the same messages: what the engine says for those typed values. */
async function typedResult(request: APIRequestContext, messages: string[]) {
  const cid = (await (await request.post('/api/conversations')).json()).conversation_id
  for (const text of messages) {
    const r = await request.post(`/api/conversations/${cid}/messages`, { data: { text } })
    expect(r.status()).toBe(200)
  }
  return (await request.get(`/api/conversations/${cid}`)).json()
}

// ---------------------------------------------------------------- the six sample employees

test.describe('reference journeys (engine = Strauss reference)', () => {
  const cases: [string, string[], string, number][] = [
    ['E-001 Noa: hybrid to electric', [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3'], 'CAR-10', 661],
    ['E-002 David: family track', ['אני נציג מכירות שטח, שכר בסיס 9,800, עמלות 2,400, אין בונוס, פנסיה 6%, אין קרן השתלמות, 2.25 נקודות זיכוי, 5 ילדים',
      'נירו', '250', 'קרניבל'], 'CAR-19', -3522],
    ['E-003 Rachel: no car with maintenance to a car', ['אני מפתחת תוכנה, שכר בסיס 21,000, אין עמלות, אין בונוס, פנסיה 6.5%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, אין ילדים',
      'אין לי רכב', '1,500', 'MG4'], 'CAR-06', -1938],
    ['E-004 Yossi: commissions, copay changes', ['אני מנהל אזור מכירות, שכר בסיס 16,000, עמלות 3,000, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 3 ילדים',
      'טוסון', '400', 'EV3'], 'CAR-11', 1207],
    ['E-005 Michal: opting out', ['אני מנהלת פיתוח, שכר בסיס 27,000, אין עמלות, אין בונוס, פנסיה 7%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 1 ילדים',
      'מאזדה 3', '350', 'ויתור'], 'OPT_OUT', 3562],
    ['E-006 Eli: bonus, high salary', ['אני מנהל מחלקה, שכר בסיס 32,000, אין עמלות, בונוס 2,000, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 4 ילדים',
      'אוקטביה', '500', 'מודל 3'], 'CAR-14', 721],
  ]
  for (const [name, messages, option, delta] of cases) {
    test(name, async ({ page }) => {
      await open(page)
      for (const m of messages) await send(page, m)
      const s = await state(page)
      expect(deltas(s)).toEqual([[option, delta]])
      // the screen shows the same amount as the server, in the agreed wording
      const word = delta > 0 ? 'יגדל' : 'יקטן'
      await expect(page.locator(BOT).last()).toContainText(`${word} ב-${Math.abs(delta).toLocaleString('en-US')} ₪`)
      if (option === 'CAR-19') expect(Number(s.results[0].result.alternative.family_share)).toBe(1150)
    })
  }
})

// ---------------------------------------------------------------- ranking and the representative

test('"הכי משתלם" ranks every eligible option and shows the top three in order', async ({ page }) => {
  await open(page)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300']) await send(page, m)
  await send(page, 'מה הכי משתלם לי?')
  await expect(page.locator(BOT).last()).toContainText('בדקתי את כל 13 הרכבים')
  const ranked = deltas(await state(page))
  expect(ranked).toHaveLength(3)
  expect(ranked.map(([, d]) => d)).toEqual([...ranked.map(([, d]) => d)].sort((a, b) => Number(b) - Number(a)))
})

test('a new car is calculated with the catalog copay, marked pending, and one question goes to the rep', async ({ page }) => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')
  await open(page)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'קורולה']) await send(page, m)
  const s = await state(page)
  const result = s.results[0].result
  expect([s.results[0].option_id, result.copay_pending, Number(result.alternative.copay)]).toEqual(['CAR-07', true, 250])
  await expect(page.locator('.tag-pending').first()).toBeVisible()
  // the rep API never says which conversation a question belongs to (privacy): COROLLA is chosen only here
  const corolla = async () => (await (await page.request.get('/api/rep/questions?status=OPEN',
    { headers: { 'X-Rep-Token': REP_TOKEN } })).json()).filter((q: { context: { catalog_code?: string } }) =>
    q.context.catalog_code === 'CAR-07')
  const mine = await corolla()
  expect(mine).toHaveLength(1)
  expect([mine[0].missing_field, mine[0].mode, Number(mine[0].suggested_value)]).toEqual(['NEW_CAR_COPAY', 'CONFIRM', 250])
  expect(Object.keys(mine[0].context).sort()).toEqual(['car_label', 'catalog_code', 'group', 'suggested_amount'])
  await send(page, 'קורולה')                                  // choosing it again opens no second question
  expect(await corolla()).toHaveLength(1)
})

test('the rep answers in the rep screen, sees no salary, and the employee sees the new amount without reloading', async ({ browser }) => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')
  const page = await (await browser.newContext()).newPage()
  await open(page)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'קונה']) await send(page, m)   // KONA EV: unique to this test
  const before = deltas(await state(page))
  expect(before[0][0]).toBe('CAR-08')

  const repPage = await (await browser.newContext()).newPage()
  await repPage.goto('/rep')
  await repPage.getByLabel('קוד גישה').fill('wrong-token')
  await repPage.getByRole('button', { name: 'כניסה' }).click()
  await expect(repPage.getByRole('alert')).toBeVisible()          // a wrong token is refused
  await repPage.getByLabel('קוד גישה').fill(REP_TOKEN)
  await repPage.getByRole('button', { name: 'כניסה' }).click()
  const card = repPage.locator('.rep-card', { hasText: 'KONA EV' }).first()
  await expect(card).toBeVisible({ timeout: 20_000 })
  await expect(card).not.toContainText('18,500')                  // the rep never sees the salary
  await expect(card).not.toContainText('18500')
  await card.getByRole('button', { name: 'סכום אחר' }).click()
  await card.locator('input[inputmode="decimal"], input[type="text"]').first().fill('300')
  await card.getByRole('button', { name: 'שליחת תשובה' }).click()

  await expect(page.locator('.event-pill').first()).toBeVisible({ timeout: 30_000 })   // polled, no reload
  const after = deltas(await state(page))
  expect(after[0][1]).toBe(Number(before[0][1]) - 50)             // KONA's catalog copay is 250: 50 more = 50 less net
})

// ---------------------------------------------------------------- payslips (recorded vision answers)

test.describe('payslip journeys', () => {
  test.beforeEach(async ({ request }) => {
    test.skip(!(await payslipEnabled(request)), 'payslip extraction is off on this server')
  })

  test('the synthetic payslip gives the base salary and shows the rest as information, nothing converted', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,000')
    await uploadPayslip(page, 'synthetic_payslip.pdf')
    await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
    await expect(row(page, 'base_salary').locator('input:not([type])')).toHaveValue('18000')
    await expect(row(page, 'base_salary')).toContainText('בתלוש: שכר יסוד')
    await expect(keep(page, 'base_salary')).toBeChecked()
    for (const f of ['commissions', 'bonus', 'pension_percent']) {
      await expect(row(page, f)).toContainText('לא נמצא בתלוש')
      await expect(keep(page, f)).not.toBeChecked()
    }
    const info = page.locator('.payslip-info')
    await expect(info).toContainText('תוספת חודשית')
    await expect(info).toContainText('לא מומר לאחוז')
    await expect(info).toContainText('לא מהנטו שבתלוש')
    await confirmPayslip(page)
    await expect(page.locator(USER).last()).toContainText('שכר בסיס 18,000 ₪')
    const p = (await state(page)).profile
    expect([p.base, p.commissions, p.bonus, p.pension_percent]).toEqual(['18000', null, null, null])
  })

  test('a payslip value that differs from the chat waits for the employee, and the result uses the confirmed one', async ({ page, request }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 15,000')
    await uploadPayslip(page, 'synthetic_payslip.pdf')
    await expect(row(page, 'base_salary')).toContainText('שונה ממה שרשמתם בשיחה: 15,000 ₪')
    await expect(keep(page, 'base_salary')).not.toBeChecked()
    await keep(page, 'base_salary').check()
    await confirmPayslip(page)
    await expect.poll(async () => (await state(page)).profile.base).toBe('18000')
    for (const m of [REST_OF_NOA, 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(page, m)
    const typed = await typedResult(request, ['מנהלת מוצר, שכר בסיס 18,000', REST_OF_NOA, 'יש לי קורולה קרוס', '300', 'אטו 3'])
    expect(deltas(await state(page))).toEqual(deltas(typed))      // same as typing 18,000: the payslip value is used
  })

  test('a payslip with every field fills the profile and the journey ends at the reference result', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
    await uploadPayslip(page, 'full_payslip.pdf')
    for (const f of ['base_salary', 'commissions', 'bonus', 'pension_percent', 'study_fund_percent', 'credit_points', 'children_under_23'])
      await expect(keep(page, f)).toBeChecked()
    await expect(row(page, 'credit_points').locator('input:not([type])')).toHaveValue('2.75')   // not cut to 2
    await confirmPayslip(page, 'אישור ושמירה (7)')
    await expect.poll(async () => (await state(page)).stage).toBe('COLLECT_CURRENT_CAR')
    for (const m of ['יש לי קורולה קרוס', '300', 'אטו 3']) await send(page, m)
    expect(deltas(await state(page))).toEqual([['CAR-10', 661]])   // the payslip's net (11,000) plays no part
  })

  test('the full synthetic payslip, with the answer Gemini gave live, fills every field as printed', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 16,000')
    await uploadPayslip(page, 'synthetic_full_payslip.pdf')
    const want: [string, string][] = [['base_salary', '16000'], ['commissions', '2400'], ['bonus', '1500'],
      ['pension_percent', '6'], ['study_fund_percent', '2.5'], ['credit_points', '2.25'], ['children_under_23', '3']]
    for (const [f, v] of want) {
      await expect(row(page, f).locator('input:not([type])')).toHaveValue(v)
      await expect(keep(page, f)).toBeChecked()
    }
    await expect(row(page, 'children_under_23')).toContainText('בתלוש: מספר ילדים עד גיל 23')
    await expect(page.locator('.payslip-info')).toContainText('קרן השתלמות עובד')
    await confirmPayslip(page, 'אישור ושמירה (7)')
    const p = (await state(page)).profile
    expect([p.base, p.commissions, p.bonus, p.pension_percent, p.study_fund_percent, p.credit_points, p.children])
      .toEqual(['16000', '2400', '1500', '6', '2.5', '2.25', 3])
  })

  test('ambiguous values are never kept on their own', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,000')
    await uploadPayslip(page, 'ambiguous_payslip.pdf')
    await expect(row(page, 'base_salary')).toContainText('שני ערכים שונים')
    await expect(row(page, 'pension_percent')).toContainText('60%')
    await expect(row(page, 'bonus')).toContainText('לא ודאי')
    for (const f of ['base_salary', 'pension_percent', 'bonus']) await expect(keep(page, f)).not.toBeChecked()
    await expect(keep(page, 'credit_points')).toBeChecked()
    await confirmPayslip(page, 'אישור ושמירה (1)')
    await expect.poll(async () => (await state(page)).profile.credit_points).toBe('2.25')
    const p = (await state(page)).profile
    expect([p.base, p.bonus, p.pension_percent]).toEqual(['18000', null, null])
  })

  test('an unclassified addition goes to bonus only when the employee assigns it', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,000')
    await uploadPayslip(page, 'synthetic_payslip.pdf')
    await page.locator('.payslip-info li[data-info="other_addition"]').getByRole('button', { name: 'לשייך לבונוס' }).click()
    await expect(row(page, 'bonus').locator('input:not([type])')).toHaveValue('1000')
    await expect(row(page, 'bonus')).toContainText('שויך על ידיכם')
    await confirmPayslip(page)
    await expect.poll(async () => (await state(page)).profile.bonus).toBe('1000')
  })

  test('a provider failure is an error with retry, and a retry with a good file recovers', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
    await uploadPayslip(page, 'quota_payslip.pdf')
    await expect(page.getByRole('heading', { name: 'לא הצלחנו לקרוא את התלוש' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('עמוס')
    await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toHaveCount(0)
    await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'full_payslip.pdf'))   // "לנסות שוב"
    await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
    expect((await state(page)).profile.pension_percent).toBeNull()  // nothing saved before the confirm
  })

  test('a file that is not a payslip says so, and manual entry still saves', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
    await uploadPayslip(page, 'not_a_payslip.pdf')
    await expect(page.getByRole('heading', { name: 'הקובץ לא נראה כמו תלוש שכר' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'לנסות קובץ אחר' })).toBeVisible()
    await page.getByRole('button', { name: 'מילוי ידני' }).click()
    await page.locator('#ps-pension_percent').fill('6')
    await confirmPayslip(page)
    await expect.poll(async () => (await state(page)).profile.pension_percent).toBe('6')
  })

  test('an unreadable file and an invalid model answer are both errors, never an empty review', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
    await uploadPayslip(page, 'unreadable_payslip.pdf')
    await expect(page.getByRole('heading', { name: 'לא הצלחנו לקרוא את הכתוב בקובץ' })).toBeVisible()
    await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'invalid_answer_payslip.pdf'))
    await expect(page.getByRole('heading', { name: 'לא הצלחנו לקרוא את התלוש' })).toBeVisible()
    await expect(page.getByRole('alert')).toContainText('תשובה לא תקינה')
    await expect(page.getByRole('button', { name: 'לנסות שוב' })).toBeVisible()
  })

  test('an unsupported file is refused by the server before any model call', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
    await page.getByRole('button', { name: 'להעלות תלוש' }).click()
    await page.locator('input[type="file"]').setInputFiles({ name: 'payslip.txt', mimeType: 'text/plain',
      buffer: Buffer.from('not a payslip') })
    await expect(page.getByRole('alert')).toContainText('אפשר להעלות רק קובץ PDF או תמונה')
  })

  test('after confirming a payslip, a reload keeps the values and the conversation goes on', async ({ page }) => {
    await open(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
    await uploadPayslip(page, 'full_payslip.pdf')
    await confirmPayslip(page, 'אישור ושמירה (7)')
    await expect.poll(async () => (await state(page)).stage).toBe('COLLECT_CURRENT_CAR')
    await page.reload()
    // the history comes back from the server, which stores the values (the "מהתלוש" prefix is the browser's
    // display text only; keeping the source on the server needs a contract change, see the report)
    await expect(page.locator(USER, { hasText: 'שכר בסיס 18,500 ₪, אין עמלות' })).toBeVisible({ timeout: 20_000 })
    const p = (await state(page)).profile
    expect([p.base, p.pension_percent, p.credit_points, p.children]).toEqual(['18500', '6', '2.75', 2])
  })
})
