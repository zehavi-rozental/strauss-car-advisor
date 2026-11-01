import { expect, test, type Locator, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

// Every field the employee or the representative types into stays readable while typing: the text colour against
// the background actually behind it has a WCAG AA contrast of at least 4.5:1, in every state of the screen.
// Found 22/10: on the opening screen the composer inherited the hero's white text on its own white box (1:1).

const __dirname = dirname(fileURLToPath(import.meta.url))
const PAYSLIPS = join(__dirname, 'fixtures', 'payslips')
const REP_TOKEN = process.env.REP_TOKEN || ''
const BOT = '.bubble-bot .bubble-text'
const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const MIN_CONTRAST = 4.5

/** Types into the field, then measures its text colour against the first opaque background behind it. */
async function contrastWhileTyping(field: Locator, text = 'בדיקה 18,500'): Promise<number> {
  await field.click()
  await field.fill(text)
  return field.evaluate((node) => {
    const parse = (c: string) => (c.match(/[\d.]+/g) || []).map(Number)
    const lum = ([r, g, b]: number[]) => {
      const f = (v: number) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)
    }
    let bg: number[] = [255, 255, 255]
    for (let n: Element | null = node; n; n = n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor)
      if (c.length === 3 || (c.length === 4 && c[3] > 0.5)) { bg = c.slice(0, 3); break }
    }
    const style = getComputedStyle(node)
    // what is painted: -webkit-text-fill-color wins over color when it is set
    const fg = parse(style.webkitTextFillColor || style.color).slice(0, 3)
    const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x)
    return (a + 0.05) / (b + 0.05)
  })
}

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, text: string) {
  await expect(page.locator('.typing')).toHaveCount(0, { timeout: 45_000 })
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

for (const viewport of [{ width: 1366, height: 860 }, { width: 375, height: 800 }]) {
  test(`the chat box is readable on the opening screen and after it (${viewport.width}px)`, async ({ browser }) => {
    const page = await (await browser.newContext({ viewport })).newPage()
    await open(page)
    const composer = page.getByLabel('הודעה')
    expect(await contrastWhileTyping(composer)).toBeGreaterThanOrEqual(MIN_CONTRAST)     // the first message
    await composer.fill('')
    await send(page, 'מנהלת מוצר, שכר בסיס 18,500')
    expect(await contrastWhileTyping(page.getByLabel('הודעה'))).toBeGreaterThanOrEqual(MIN_CONTRAST)
    await page.context().close()
  })
}

test('the correction form fields are readable', async ({ page }) => {
  await open(page)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(page, m)
  await page.getByRole('button', { name: 'פירוט החישוב' }).first().click()
  await page.getByRole('dialog', { name: 'טבלת השוואה' }).getByRole('button', { name: 'תיקון נתונים' }).click()
  for (const id of ['#pf-bonus', '#pf-pension_percent']) {
    expect(await contrastWhileTyping(page.locator(id), '1200'), id).toBeGreaterThanOrEqual(MIN_CONTRAST)
  }
})

test('the payslip review fields are readable', async ({ page, request }) => {
  test.skip(!(await (await request.get('/api/health')).json()).payslip, 'payslip extraction is off on this server')
  await open(page)
  await send(page, 'מנהלת מוצר, שכר בסיס 15,000')
  await page.getByRole('button', { name: 'להעלות תלוש' }).click()
  await page.locator('input[type="file"]').setInputFiles(join(PAYSLIPS, 'synthetic_payslip.pdf'))
  await expect(page.getByRole('heading', { name: 'בדיקת הנתונים מהתלוש' })).toBeVisible()
  expect(await contrastWhileTyping(page.locator('#ps-base_salary'), '18000')).toBeGreaterThanOrEqual(MIN_CONTRAST)
})

test('the representative’s fields are readable', async ({ browser }) => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')
  const page = await (await browser.newContext()).newPage()
  await open(page)
  // this test's own question (a new car's copay the employee asks about): never another spec's last card
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3', 'אני רוצה שנציג התחבורה יאשר את ההשתתפות']) await send(page, m)
  const rep = await (await browser.newContext()).newPage()
  await rep.goto('/rep')
  expect(await contrastWhileTyping(rep.getByLabel('קוד גישה'), 'abc')).toBeGreaterThanOrEqual(MIN_CONTRAST)
  await rep.getByLabel('קוד גישה').fill(REP_TOKEN)
  await rep.getByRole('button', { name: 'כניסה' }).click()
  const card = rep.locator('.rep-card', { hasText: 'ATTO 3' }).last()
  await expect(card).toBeVisible({ timeout: 20_000 })
  // the note is optional (29/10); its field is as readable as any other
  expect(await contrastWhileTyping(card.getByLabel('הערה לעובד (לא חובה)'), 'הערה')).toBeGreaterThanOrEqual(MIN_CONTRAST)
  await card.getByRole('button', { name: 'סכום אחר' }).click()
  // "סכום (₪)", or "סכום ההשתתפות (₪)" on a new car's copay
  expect(await contrastWhileTyping(card.getByLabel(/^סכום( ההשתתפות)? \(₪\)$/), '300')).toBeGreaterThanOrEqual(MIN_CONTRAST)
  await rep.context().close()
  await page.context().close()
})
