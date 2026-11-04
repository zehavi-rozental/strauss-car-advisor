// Not a check: pictures of every screen, on a desktop and on a phone, for a person to look at. Runs only with SCREENS_DIR set.
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const DIR = process.env.SCREENS_DIR || ''
const REP_TOKEN = process.env.REP_TOKEN || ''
const BOT = '.bubble-bot .bubble-text'
const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'

async function send(page: Page, text: string) {
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(() => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 45_000 })
}

for (const [name, width, height] of [['d', 1280, 800], ['m', 375, 760]] as const) {
  test.describe(`screens ${name}`, () => {
    test.skip(!DIR, 'SCREENS_DIR')
    test.use({ viewport: { width, height } })
    const shot = (page: Page, label: string, fullPage = false) => page.screenshot({ path: `${DIR}/${name}-${label}.png`, fullPage })

    test('employee', async ({ page }) => {
      test.setTimeout(180_000)
      await page.goto('/')
      await page.waitForTimeout(800)
      await shot(page, '00-entry')
      await page.goto('/employee')
      await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
      await page.waitForTimeout(600)
      await shot(page, '01-opening')
      await send(page, NOA)
      await shot(page, '02-collecting')
      await send(page, 'יש לי קורולה קרוס')
      await send(page, '300')
      await shot(page, '03-choosing')
      await send(page, 'מה הכי משתלם לי?')
      await page.waitForTimeout(400)
      await shot(page, '04-results')
      await page.getByRole('button', { name: /הנתונים שלי/ }).click()
      await page.waitForTimeout(400)
      await shot(page, '05-sheet')
      await page.getByRole('dialog', { name: 'הנתונים שלי' }).getByRole('button', { name: 'לעדכן: שכר בסיס' }).click()
      await page.waitForTimeout(200)
      await shot(page, '06-editor')
      await page.getByRole('dialog', { name: 'הנתונים שלי' }).getByRole('textbox').fill('20000')
      await page.getByRole('dialog', { name: 'הנתונים שלי' }).getByRole('button', { name: 'לעדכן', exact: true }).click()
      await expect(page.getByRole('region', { name: 'מה השתנה' }).last()).toBeVisible()
      await page.waitForTimeout(400)
      await shot(page, '07-changed')
      await send(page, 'מה יקרה אם ארוויח 25,000?')
      await page.waitForTimeout(300)
      await shot(page, '08-check')
      await page.getByRole('button', { name: 'להשאיר כבדיקה' }).click()
      await page.waitForTimeout(500)
      await send(page, 'MODEL 3')
      await shot(page, '09-not-available')
      const table = page.getByRole('button', { name: /טבלה|פירוט|השוואה מלאה|לפתוח/ }).first()
      if (await table.count()) {
        await table.click().catch(() => undefined)
        await page.waitForTimeout(500)
        await shot(page, '10-comparison')
        await page.keyboard.press('Escape')
      }
      await page.getByRole('button', { name: 'כל הרכבים לפי נטו' }).click()
      await page.waitForTimeout(800)
      const open = page.getByRole('button', { name: 'פתיחת מפת האפשרויות' })
      if (await open.count()) {
        await open.click()
        await page.waitForTimeout(500)
        await shot(page, '11-map')
        await page.keyboard.press('Escape')
      }
      await page.getByRole('button', { name: 'שיחה חדשה' }).click()
      await page.waitForTimeout(300)
      await shot(page, '12-confirm')
      await page.getByRole('alertdialog').getByRole('button', { name: 'ביטול' }).click()
      const other = await page.context().newPage()
      await other.goto('/employee')
      await expect(other.getByRole('region', { name: 'שיחה שמורה' })).toBeVisible({ timeout: 30_000 })
      await other.screenshot({ path: `${DIR}/${name}-13-resume.png` })
      await other.close()
    })

    test('rep', async ({ page, browser }) => {
      test.setTimeout(180_000)
      const employee = await (await browser.newContext({ viewport: { width, height } })).newPage()
      await employee.goto('/employee')
      await expect(employee.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
      for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'NIRO']) await send(employee, m)
      await send(employee, 'האם יש ל-NIRO גג נפתח?')
      await employee.waitForTimeout(300)
      await employee.screenshot({ path: `${DIR}/${name}-14-offer.png` })
      await employee.getByRole('button', { name: /לשאול את נציג הרכב/ }).click()
      await employee.waitForTimeout(400)
      await employee.screenshot({ path: `${DIR}/${name}-15-inquiry.png` })
      const card = employee.locator('.rep-inquiry-card')
      await card.getByRole('button', { name: 'המשך' }).click()
      await card.getByRole('button', { name: 'שליחת הפנייה לנציג' }).click()
      await expect(employee.getByTestId('rep-inquiry-history').last()).toBeVisible({ timeout: 20_000 })
      await employee.waitForTimeout(400)
      await employee.screenshot({ path: `${DIR}/${name}-16-record.png` })

      await page.goto('/rep')
      await page.waitForTimeout(400)
      await shot(page, '17-rep-login')
      await page.getByLabel('קוד גישה').fill(REP_TOKEN)
      await page.getByRole('button', { name: 'כניסה' }).click()
      await page.waitForTimeout(1200)
      await shot(page, '18-rep-list')
      const q = page.locator('.rep-card', { hasText: 'גג נפתח' }).last()
      await q.scrollIntoViewIfNeeded()
      await q.screenshot({ path: `${DIR}/${name}-19-rep-card.png` })
      await employee.context().close()
    })
  })
}
