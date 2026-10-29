import { expect, test } from '@playwright/test'
import type { Browser, Page } from '@playwright/test'

// The representative flow end-to-end in the browser (stage 2, 22/10): a new car is calculated at once with the catalog
// copay and marked as waiting for the representative; the representative approves it in one click, or sets another
// amount from a date; the employee's comparison updates without a reload and keeps it after one. A stated group that
// contradicts the occupations table waits for the representative's group. Requires REP_TOKEN (not printed).
// Cars used only here: MAZDA 3 (CAR-13) and BYD DOLPHIN (CAR-03), so other specs' open questions do not interfere.

const REP_TOKEN = process.env.REP_TOKEN || ''
const BOT = '.bubble-bot .bubble-text'
const PROFILE = 'מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'

async function employee(browser: Browser) {
  const page = await (await browser.newContext()).newPage()
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  return page
}

async function send(page: Page, text: string) {
  const n = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(n)
}

async function repScreen(browser: Browser) {
  const context = await browser.newContext()
  const rep = await context.newPage()
  await rep.goto('/rep')
  await rep.getByLabel('קוד גישה').fill(REP_TOKEN)
  await rep.getByRole('button', { name: 'כניסה' }).click()
  return { rep, close: () => context.close() }      // the rep screen polls: an open one uses up the rate limit
}

test.describe('representative flow', () => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')

  test('the catalog copay is used at once, the rep approves it in one click, and the label changes', async ({ browser }) => {
    const page = await employee(browser)
    for (const m of [PROFILE, 'יש לי קורולה קרוס, משלמת 300', 'MAZDA 3']) await send(page, m)
    // the rule since 22/10: the catalog amount is used and said; the employee asks the representative to confirm it
    await expect(page.locator(BOT).last()).toContainText('לפי הקטלוג, ההשתתפות ברכב הזה היא 350 ₪')
    await send(page, 'אני רוצה שנציג התחבורה יאשר את ההשתתפות')
    const card = page.locator('.result-card').last()
    await expect(card).toContainText('השתתפות לפי ברירת המחדל בקטלוג — ממתינה לאישור הנציג')
    const copay = card.locator('.copay-box')
    await expect(copay).toContainText('350')
    await expect(copay).toContainText('לפי ברירת המחדל בקטלוג')
    await expect(copay).toContainText('ממתינה לאישור הנציג')
    await expect(page.locator(BOT).last()).toContainText('פניתי לנציג התחבורה לבירור ההשתתפות')

    const { rep, close } = await repScreen(browser)
    const q = rep.locator('.rep-card', { hasText: 'MAZDA 3' }).last()
    await expect(q).toBeVisible({ timeout: 20_000 })
    await expect(q).not.toContainText('18,500')
    await expect(q.getByLabel('ההשתתפות חלה מתאריך')).toBeVisible()
    await q.getByRole('button', { name: /אישור סכום ושליחת תשובה/ }).click()
    await close()

    await expect(page.locator(BOT, { hasText: 'נציג התחבורה אישר את ההשתתפות ב-MAZDA 3 (2025): 350 ₪ בחודש.' }))
      .toBeVisible({ timeout: 30_000 })                                    // polled, no reload
    await expect(page.locator('.result-card').last().locator('.state-approved')).toHaveText('אושר על ידי הנציג')
    await page.reload()                                                    // the approval survives a refresh
    await expect(page.locator('.result-card').last().locator('.copay-box')).toContainText('אושר על ידי הנציג')
  })

  test('another amount from a future date recalculates, and the date is never shown as in force today', async ({ browser }) => {
    const page = await employee(browser)
    for (const m of [PROFILE, 'יש לי קורולה קרוס, משלמת 300', 'DOLPHIN', 'אני רוצה שנציג התחבורה יאשר את ההשתתפות']) await send(page, m)
    await expect(page.locator('.result-card').last().locator('.copay-box')).toContainText('200')
    const year = new Date().getFullYear() + 1

    const { rep, close } = await repScreen(browser)
    const q = rep.locator('.rep-card', { hasText: 'BYD DOLPHIN' }).last()
    await expect(q).toBeVisible({ timeout: 20_000 })
    await q.getByRole('button', { name: 'סכום אחר' }).click()
    await q.locator('input[inputmode="decimal"]').fill('260')
    await q.getByLabel('ההשתתפות חלה מתאריך').fill(`${year}-01-01`)
    await q.getByRole('button', { name: 'שליחת תשובה' }).click()
    await close()

    const answer = page.locator(BOT, { hasText: 'נציג התחבורה קבע השתתפות של 260 ₪ בחודש ב-BYD DOLPHIN (2025)' })
    await expect(answer).toBeVisible({ timeout: 30_000 })
    await expect(answer).toContainText(`ההשתתפות תחול מ-01.01.${year}.`)
    await expect(answer).toContainText('חישבתי מחדש')
    await expect(answer).not.toContainText('בתוקף')
    const copay = page.locator('.result-card').last().locator('.copay-box')
    await expect(copay).toContainText('260')
    await expect(copay).toContainText(`תחול מ-01.01.${year}`)
    await expect(copay).toContainText('אושר על ידי הנציג')
  })

  test('a stated group that contradicts the occupations table waits for the rep, who answers with a button', async ({ browser }) => {
    const page = await employee(browser)
    for (const m of ['קבוצה ב, שכר בסיס 18,500', 'אני מנהלת מוצר',
                     'אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים',
                     'יש לי קורולה קרוס, משלמת 300']) await send(page, m)
    await expect(page.locator(BOT, { hasText: 'את זה לא אכריע בעצמי' })).toBeVisible()

    const { rep, close } = await repScreen(browser)
    const q = rep.locator('.rep-card', { hasText: 'קבוצת זכאות שלא תואמת לתפקיד' }).last()
    await expect(q).toBeVisible({ timeout: 20_000 })
    await expect(q).not.toContainText('18,500')
    await expect(q.getByLabel('ההשתתפות חלה מתאריך')).toHaveCount(0)     // a date only for a new car's copay
    await q.getByRole('button', { name: 'לפי טבלת העיסוקים: קבוצה ג' }).click()
    await close()

    await expect(page.locator(BOT, { hasText: 'נציג התחבורה השיב: קבוצת הזכאות שלכם היא ג.' }))
      .toBeVisible({ timeout: 30_000 })
    await expect(page.locator(BOT).last()).toContainText('איזה רכבים להשוות')
  })
})
