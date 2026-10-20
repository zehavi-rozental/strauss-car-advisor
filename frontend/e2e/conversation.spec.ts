import { expect, test, type Page } from '@playwright/test'

// Real browser e2e against the running frontend (Vite) + backend (uvicorn :8000, live Gemini).
// These exercise the employee UI the way a person does: type in the composer, read the bubbles.
// The live model is non-deterministic, so assertions check BEHAVIOUR (what is saved, progress, no loop,
// no duplicate bubble), not exact wording.

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user .bubble-text'

async function openEmployee(page: Page) {
  await page.goto('/employee')
  // wait for the greeting bubble
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

async function send(page: Page, text: string) {
  const box = page.getByLabel('הודעה')
  await box.click()
  await box.fill(text)
  const botCountBefore = await page.locator(BOT).count()
  await page.getByLabel('שליחה').click()
  // wait until a new bot bubble appears (the reply), allowing for the LLM round-trip
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 })
    .toBeGreaterThan(botCountBefore)
}

async function lastBot(page: Page): Promise<string> {
  return (await page.locator(BOT).last().innerText()).trim()
}

/** No bubble contains the same non-empty line twice. */
async function assertNoDuplicateLineInBubbles(page: Page) {
  const texts = await page.locator(BOT).allInnerTexts()
  for (const t of texts) {
    const lines = t.split('\n').map((l) => l.trim()).filter(Boolean)
    expect(new Set(lines).size, `duplicate line in a bubble:\n${t}`).toBe(lines.length)
  }
}

test.describe('employee conversation', () => {
  test('greeting invites group, car and interest', async ({ page }) => {
    await openEmployee(page)
    const greeting = await page.locator(BOT).first().innerText()
    expect(greeting).toContain('קבוצת הזכאות')
  })

  test('multi-message profile, defer salary, no re-ask loop', async ({ page }) => {
    await openEmployee(page)
    await send(page, 'קבוצה ב אין לי רכב')
    await send(page, 'את השכר אבדוק אחר כך, תשאל משהו אחר')
    const afterDefer = await lastBot(page)
    // the immediate next question must not demand the base salary again
    const lastLine = afterDefer.split('\n').filter((l) => l.trim()).pop() || ''
    expect(lastLine, `re-asked salary:\n${afterDefer}`).not.toContain('שכר הבסיס')
    await assertNoDuplicateLineInBubbles(page)
  })

  test('correction keeps a single value', async ({ page }) => {
    await openEmployee(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18500')
    await send(page, 'פנסיה 6%')
    await send(page, 'פנסיה 7%')
    // verify via the server view that pension is 7 (the UI does not show it, so check the API through the page)
    const pension = await page.evaluate(async () => {
      const id = localStorage.getItem('sca.conversationId')
      const r = await fetch(`/api/conversations/${id}`, { credentials: 'include' })
      const v = await r.json()
      return v.profile.pension_percent
    })
    expect(String(pension)).toBe('7')
  })

  test('no-car employee is asked about a maintenance allowance (3-way), not presumed', async ({ page }) => {
    await openEmployee(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים')
    await send(page, 'אין לי רכב')
    const q = await lastBot(page)
    expect(q).toContain('אחזקת רכב')
    expect(q.includes('אין') || q.includes('לא ידוע')).toBeTruthy()
    await assertNoDuplicateLineInBubbles(page)
  })

  test('off-topic question keeps state and does not derail', async ({ page }) => {
    await openEmployee(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18500')
    await send(page, 'מה מזג האוויר מחר?')
    // state preserved: the server still has the base salary
    const base = await page.evaluate(async () => {
      const id = localStorage.getItem('sca.conversationId')
      const r = await fetch(`/api/conversations/${id}`, { credentials: 'include' })
      return (await r.json()).profile.base
    })
    expect(String(base)).toBe('18500')
  })

  test('refresh restores the conversation', async ({ page }) => {
    await openEmployee(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18500')
    const before = await page.locator(USER).count()
    await page.reload()
    await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
    // the user message survives the reload
    await expect.poll(async () => page.locator(USER).count(), { timeout: 20_000 }).toBeGreaterThanOrEqual(before)
    const base = await page.evaluate(async () => {
      const id = localStorage.getItem('sca.conversationId')
      const r = await fetch(`/api/conversations/${id}`, { credentials: 'include' })
      return (await r.json()).profile.base
    })
    expect(String(base)).toBe('18500')
  })

  test('full journey to a result with an alternative', async ({ page }) => {
    await openEmployee(page)
    await send(page, 'מנהלת מוצר, שכר בסיס 18500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים')
    await send(page, 'יש לי קורולה קרוס, משלמת 300')
    await send(page, 'אטו 3')
    // a result card appears (net today / alternative / delta)
    await expect(page.locator('.result-cards, .summary-card, .result-card').first())
      .toBeVisible({ timeout: 45_000 })
    await assertNoDuplicateLineInBubbles(page)
  })
})
