import { expect, test } from '@playwright/test'

// The representative flow end-to-end in the browser: the employee reaches a point where the current car's
// copay is unknown -> a rep question is created -> the rep answers it in the /rep portal -> the employee's
// conversation resumes and a result appears. Requires REP_TOKEN in the environment (not printed).

const REP_TOKEN = process.env.REP_TOKEN || ''
const BOT = '.bubble-bot .bubble-text'

test.describe('representative flow', () => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')

  test('no-copay goes to the rep, rep answers, employee resumes with a result', async ({ browser }) => {
    // --- employee context ---
    const emp = await browser.newContext()
    const page = await emp.newPage()
    await page.goto('/employee')
    await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })

    const send = async (text: string) => {
      const n = await page.locator(BOT).count()
      await page.getByLabel('הודעה').fill(text)
      await page.getByLabel('שליחה').click()
      await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(n)
    }

    await send('מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים')
    await send('יש לי קורולה קרוס')
    await send('לא יודעת')                 // current copay unknown -> a rep question opens (WAITING_FOR_REP)
    // a car named while waiting is clarified (not guessed): say it is an alternative
    await send('אטו 3')                    // -> "is this your car or an alternative?"
    await send('חלופה')                    // -> queued as an alternative, still waiting for the rep

    // the employee is now waiting for the representative
    await expect(page.locator('.header-tag, .tag-pending').first()).toBeVisible({ timeout: 20_000 })

    // --- rep context: log in via sessionStorage token, answer the current-car copay question(s) ---
    // NOTE: the dev backend shares one SQLite DB, so other open questions from earlier runs may be listed.
    // We answer every open "השתתפות נוכחית" (current copay) card so this conversation's question is covered.
    const rep = await browser.newContext()
    const repPage = await rep.newPage()
    await repPage.addInitScript((tok) => sessionStorage.setItem('sca.repToken', tok), REP_TOKEN)
    await repPage.goto('/rep')
    await expect(repPage.locator('.rep-card').first()).toBeVisible({ timeout: 20_000 })
    const currentCopayCards = repPage.locator('.rep-card', { hasText: 'השתתפות עובד ברכב הנוכחי' })
    const count = await currentCopayCards.count()
    for (let i = 0; i < count; i++) {
      const card = currentCopayCards.nth(0)   // the list re-renders after each answer; always take the first open one
      if (!(await card.isVisible().catch(() => false))) break
      const other = card.getByRole('button', { name: 'סכום אחר' })
      if (await other.count()) await other.click()
      const input = card.locator('input[inputmode="decimal"], input[type="text"]').first()
      await input.fill('250')
      await card.getByRole('button', { name: 'שליחת תשובה' }).click()
      await repPage.waitForTimeout(1500)
    }

    // --- back on the employee: the answer arrives (polling, up to a few cycles) and a result shows ---
    await expect(page.locator('.event-pill').first()).toBeVisible({ timeout: 30_000 })
    await expect(page.locator('.result-cards, .summary-card, .result-card').first())
      .toBeVisible({ timeout: 30_000 })

    await emp.close()
    await rep.close()
  })
})
