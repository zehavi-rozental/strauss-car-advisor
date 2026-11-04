// The review of 4/11, in the browser: one value edited on its own, a check that saves nothing, what is known, a saved conversation
// that asks before it shows anything, deletion, the three ways to see the cars, a phone, accessibility. Synthetic data only.
// The figures are compared with the server's own results, never with a number written here.
import { expect, test } from '@playwright/test'
import type { Browser, Page } from '@playwright/test'
import AxeBuilder from '@axe-core/playwright'

const REP_TOKEN = process.env.REP_TOKEN || ''
const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const PROFILE = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, text: string) {
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
  const users = await page.locator(USER).count()
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await expect(page.getByLabel('שליחה')).toBeEnabled({ timeout: 45_000 })
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 45_000 }).toBeGreaterThan(users)
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

async function toResults(page: Page) {
  await open(page)
  for (const m of [PROFILE, 'יש לי קורולה קרוס', '300', 'מה הכי משתלם לי?']) await send(page, m)
  await expect(page.locator(BOT).last()).toContainText('בדקתי את כל')
}

async function state(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const r = await page.request.get(`/api/conversations/${id}`)
  expect(r.status()).toBe(200)
  return r.json()
}

const shown = (n: string | number) => Math.abs(Math.round(Number(n))).toLocaleString('en-US')
const salaryChip = (page: Page, value: string) => page.getByRole('button', { name: new RegExp(`שכר בסיס: ${value}`) })
const SHEET = { name: 'הנתונים שלי' }

test.describe('one value, on its own', () => {
  test('the salary changes: the figures before and after are the engine\'s, the rest of the data stays', async ({ page }) => {
    await toResults(page)
    const before = await state(page)
    await salaryChip(page, '18,500').click()
    const sheet = page.getByRole('dialog', SHEET)
    await sheet.getByRole('textbox').fill('20000')
    await sheet.getByRole('button', { name: 'לעדכן', exact: true }).click()
    await expect(sheet).toHaveCount(0)
    await expect(salaryChip(page, '20,000')).toBeVisible()

    const card = page.getByRole('region', { name: 'מה השתנה' }).last()
    await expect(card).toContainText('18,500')
    await expect(card).toContainText('20,000')
    const after = await state(page)
    expect(after.profile.base).toBe('20000')
    expect(after.profile.pension_percent).toBe(before.profile.pension_percent)   // nothing else moved
    expect(after.current_car.catalog_code).toBe(before.current_car.catalog_code)
    await expect(card).toContainText(shown(before.results[0].result.today.net_display))
    await expect(card).toContainText(shown(after.results[0].result.today.net_display))
    for (const r of after.results) {
      await expect(card).toContainText(shown(r.result.delta_display))
    }
    await expect(page.locator('.superseded-tag').first()).toBeVisible()     // the earlier report is marked

    await send(page, 'מה רשמת?')
    await expect(page.locator(BOT).last()).toContainText('20,000')
  })

  test('out of range: said at the field, nothing saved, what was typed stays', async ({ page }) => {
    await toResults(page)
    await salaryChip(page, '18,500').click()
    const sheet = page.getByRole('dialog', SHEET)
    await sheet.getByRole('textbox').fill('99999999')
    await sheet.getByRole('button', { name: 'לעדכן', exact: true }).click()
    await expect(sheet.getByRole('alert')).toBeVisible()
    await expect(sheet.getByRole('textbox')).toHaveValue('99999999')
    expect((await state(page)).profile.base).toBe('18500')
  })

  test('the sheet lists what is known and missing, with where it came from', async ({ page }) => {
    await open(page)
    await send(page, 'שכר בסיס 18,500')
    await page.getByRole('button', { name: /הנתונים שלי/ }).click()
    const sheet = page.getByRole('dialog', SHEET)
    await expect(sheet).toContainText('18,500')
    await expect(sheet).toContainText('מהשיחה')
    await expect(sheet).toContainText('חסר')
    await expect(sheet).toContainText('טעונה אימות')    // the rules version is marked as needing verification
  })
})

test.describe('a real change or only a check', () => {
  test('a what-if saves nothing, says so, and is saved only on request', async ({ page }) => {
    await toResults(page)
    const before = await state(page)
    await send(page, 'מה יקרה אם ארוויח 20,000?')
    await expect(page.getByRole('region', { name: 'בדיקה בלבד' }).last()).toContainText('הנתונים שלכם לא השתנו')
    await expect(salaryChip(page, '18,500')).toBeVisible()
    let now = await state(page)
    expect(now.profile.base).toBe('18500')
    expect(now.results.map((r: { result: unknown }) => r.result)).toEqual(before.results.map((r: { result: unknown }) => r.result))

    await page.getByRole('button', { name: 'להשאיר כבדיקה' }).click()
    await expect(page.getByRole('button', { name: 'להשאיר כבדיקה' })).toHaveCount(0)
    expect((await state(page)).profile.base).toBe('18500')

    await send(page, 'ואם הייתי מרוויח 21,000?')
    await page.getByRole('button', { name: 'לעדכן כנתון שלי' }).click()
    await expect(salaryChip(page, '21,000')).toBeVisible()
    now = await state(page)
    expect(now.profile.base).toBe('21000')
  })

  test('a number with no clear intent is asked', async ({ page }) => {
    await toResults(page)
    await send(page, '20,000')
    await expect(page.locator(BOT).last()).toContainText('או רק לבדוק')
    await page.getByRole('button', { name: 'רק לבדוק' }).click()
    await expect(page.getByRole('region', { name: 'בדיקה בלבד' }).last()).toBeVisible()
    expect((await state(page)).profile.base).toBe('18500')
  })
})

test.describe('a shared computer', () => {
  test('a reload keeps the tab; another tab asks before showing anything, and can erase', async ({ page, context }) => {
    await toResults(page)
    await page.reload()
    await expect(salaryChip(page, '18,500')).toBeVisible({ timeout: 30_000 })

    const other = await context.newPage()
    await other.goto('/employee')
    await expect(other.getByRole('region', { name: 'שיחה שמורה' })).toBeVisible({ timeout: 30_000 })
    await expect(other.locator('body')).not.toContainText('18,500')
    await other.getByRole('button', { name: 'למחוק ולהתחיל שיחה חדשה' }).click()
    await expect(other.getByRole('region', { name: 'שיחה שמורה' })).toHaveCount(0, { timeout: 30_000 })
    await expect(other.locator(BOT).first()).toBeVisible()
    await expect(other.locator('body')).not.toContainText('18,500')
    await other.close()

    await page.reload()     // the first tab's conversation is gone from the server too
    await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('body')).not.toContainText('18,500')
  })

  test('the other tab can go on with it after choosing', async ({ page, context }) => {
    await toResults(page)
    const other = await context.newPage()
    await other.goto('/employee')
    await other.getByRole('button', { name: 'להמשיך את השיחה' }).click()
    await expect(salaryChip(other, '18,500')).toBeVisible({ timeout: 30_000 })
    await other.close()
  })

  test('a new conversation says it erases this one, and nothing of it stays', async ({ page }) => {
    await toResults(page)
    const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
    await page.getByRole('button', { name: 'שיחה חדשה' }).click()
    const dialog = page.getByRole('alertdialog')
    await expect(dialog).toContainText('יימחקו')
    await dialog.getByRole('button', { name: 'ביטול' }).click()
    await expect(salaryChip(page, '18,500')).toBeVisible()

    await page.getByRole('button', { name: 'שיחה חדשה' }).click()
    await page.getByRole('alertdialog').getByRole('button').first().click()      // the first is the action, the last cancels
    await expect(salaryChip(page, '18,500')).toHaveCount(0, { timeout: 30_000 })
    expect((await page.request.get(`/api/conversations/${id}`)).status()).toBeGreaterThanOrEqual(400)
    await page.reload()
    await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('body')).not.toContainText('18,500')
  })

  test('deleting from the data sheet', async ({ page }) => {
    await toResults(page)
    const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
    await page.getByRole('button', { name: /הנתונים שלי/ }).click()
    await page.getByRole('dialog', SHEET).getByRole('button', { name: /מחיקת השיחה/ }).click()
    await page.getByRole('alertdialog').getByRole('button').first().click()
    await expect(salaryChip(page, '18,500')).toHaveCount(0, { timeout: 30_000 })
    expect((await page.request.get(`/api/conversations/${id}`)).status()).toBeGreaterThanOrEqual(400)
  })
})

test.describe('the three ways to see the cars', () => {
  test('one group, one clear state, and the keyboard reaches each', async ({ page }) => {
    await toResults(page)
    const group = page.getByRole('group', { name: 'איך להציג את הרכבים' })
    await expect(group.getByRole('button', { name: '3 המשתלמים ביותר' })).toHaveAttribute('aria-pressed', 'true')
    await expect(group.locator('[aria-pressed="true"]')).toHaveCount(1)
    await group.getByRole('button', { name: 'לשנות בחירה' }).focus()
    await page.keyboard.press('Tab')
    await expect(group.getByRole('button', { name: '3 המשתלמים ביותר' })).toBeFocused()
    await page.keyboard.press('Tab')
    await expect(group.getByRole('button', { name: 'כל הרכבים לפי נטו' })).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.locator(BOT).last()).toContainText('מיפיתי את כל', { timeout: 45_000 })
  })
})

test.describe('a phone', () => {
  test.use({ viewport: { width: 375, height: 800 } })

  test('nothing scrolls sideways, and the sheet fits', async ({ page }) => {
    await toResults(page)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.getByRole('button', { name: /הנתונים שלי/ }).click()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    const box = await page.getByRole('dialog', SHEET).boundingBox()
    expect(box !== null && box.width <= 375).toBe(true)
  })
})

test.describe('accessibility', () => {
  const serious = async (page: Page) => {
    const { violations } = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()
    return violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
      .map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0]?.target} ${v.nodes[0]?.any[0]?.message ?? ''}`)
  }

  test('the chat with results and the data sheet have no serious violations', async ({ page }) => {
    await toResults(page)
    expect(await serious(page)).toEqual([])
    await page.getByRole('button', { name: /הנתונים שלי/ }).click()
    const sheet = page.getByRole('dialog', SHEET)
    await expect(sheet).toBeVisible()
    await sheet.evaluate((el) => Promise.all(el.getAnimations({ subtree: true }).map((a) => a.finished)))   // measured at rest
    expect(await serious(page)).toEqual([])
  })

  test('the page reads right to left', async ({ page }) => {
    await open(page)
    expect(await page.evaluate(() => document.documentElement.dir)).toBe('rtl')
  })
})

test.describe('the representative', () => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')

  async function repScreen(browser: Browser) {
    const context = await browser.newContext()
    const rep = await context.newPage()
    await rep.goto('/rep')
    await rep.getByLabel('קוד גישה').fill(REP_TOKEN)
    await rep.getByRole('button', { name: 'כניסה' }).click()
    return { rep, close: () => context.close() }
  }

  test('an amount far from the catalog is confirmed once, then marked and not ranked', async ({ browser }) => {
    const page = await (await browser.newContext()).newPage()
    await open(page)
    for (const m of [PROFILE, 'יש לי קורולה קרוס', '300', 'קורולה', 'אני רוצה שנציג התחבורה יאשר את ההשתתפות']) await send(page, m)
    const { rep, close } = await repScreen(browser)
    const q = rep.locator('.rep-card', { hasText: /COROLLA/i }).last()
    await expect(q).toBeVisible({ timeout: 20_000 })
    await q.getByRole('button', { name: 'סכום אחר' }).click()
    await q.getByLabel('סכום ההשתתפות החודשי (₪)').fill('350000')
    await q.getByRole('button', { name: /שליחת תשובה/ }).click()
    const ask = q.getByRole('alert')
    await expect(ask).toBeVisible()
    expect((await state(page)).results[0].result.unusual_copay ?? false).toBe(false)    // not saved yet
    await q.getByRole('button', { name: 'זה הסכום החודשי, לאשר' }).click()
    await close()
    await expect.poll(async () => (await state(page)).results[0].result.unusual_copay, { timeout: 30_000 }).toBe(true)
    await page.context().close()
  })
})
