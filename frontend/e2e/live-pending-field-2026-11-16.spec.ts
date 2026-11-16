import { expect, test, type Page } from '@playwright/test'

// One continuous conversation against the ISOLATED test server (127.0.0.1:8892, live Gemini, its own DB and fake
// mail), replaying the manual check of 16/11: an explanation in the middle of the contributions, a bare "5", "אין"
// for the study fund, no comparison before the data is complete, "הכי משתלם" with cards whose numbers match the chat,
// and a copay correction from 300 to 250 said once and recalculated. Every step checks the server's state too.

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'

test.skip(!/127\.0\.0\.1:889[23]/.test(process.env.E2E_BASE_URL || ''), 'isolated live server only')

async function send(page: Page, text: string): Promise<string> {
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 60_000 })
  const users = await page.locator(USER).count()
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await expect(page.getByLabel('שליחה')).toBeEnabled({ timeout: 60_000 })
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 60_000 }).toBeGreaterThan(users)
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 60_000 }).toBeGreaterThan(bots)
  await expect(page.locator('.typing, .bubble-typing')).toHaveCount(0, { timeout: 60_000 })
  const all = await page.locator(BOT).allInnerTexts()
  return all.slice(bots).join('\n')
}

async function view(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const r = await page.request.get(`/api/conversations/${id}`)
  expect(r.status()).toBe(200)
  return r.json()
}

const shekels = (v: string | number) => Math.round(Number(v)).toLocaleString('en-US')
const cards = (page: Page) => page.locator('.result-card, article[aria-label="סיכום ההשוואה"]')
const questions = (reply: string) => reply.split('\n').filter((l) => l.includes('?'))

test('live: pending fields, an explanation, the comparison cards and a copay correction', async ({ page }) => {
  test.setTimeout(420_000)
  const log: string[] = []
  const say = async (text: string) => {
    const reply = await send(page, text)
    log.push(`> ${text}\n${reply}`)
    return reply
  }
  try {
    await page.goto('/employee')
    await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })

    await say('מפתחת תוכנה, קבוצה ב, יש לי רכב חברה')
    await say('בסיס 20000, אין עמלות, אין בונוס')

    // 1. an explanation in the middle of the data: grounded, and back to the field that waits
    const explained = await say('מה זה קרן השתלמות?')
    expect(explained).toContain('חיסכון')
    expect(explained).toMatch(/מהנטו|מהשכר/)                         // what it means for the pay, in any wording
    expect(questions(explained).length).toBeLessThanOrEqual(1)
    let s = await view(page)
    expect(s.profile.study_fund_percent).toBeNull()                   // a question is never an answer
    expect(s.profile.pension_percent).toBeNull()

    // 2. a bare "5": the pension only (5% is outside the study fund's range); the study fund is still asked
    const five = await say('5')
    s = await view(page)
    expect(Number(s.profile.pension_percent)).toBe(5)
    expect(s.profile.study_fund_percent).toBeNull()
    expect(s.stage).toBe('COLLECT_PROFILE')
    expect(five).toContain('קרן השתלמות')
    expect(five).not.toContain('חסר עוד')

    // 3. "אין" for the study fund, named: 0, said once
    const none = await say('אין לי קרן השתלמות')
    s = await view(page)
    expect(Number(s.profile.study_fund_percent)).toBe(0)
    expect(none.match(/אין קרן השתלמות/g)?.length ?? 0).toBeLessThanOrEqual(1)

    await say('2.25 נקודות זיכוי, אין ילדים')
    s = await view(page)
    expect(Number(s.profile.credit_points)).toBe(2.25)
    expect(Number(s.profile.children)).toBe(0)

    // 4. today's car: no cards yet
    await say('קורולה קרוס 2025, משלמת 300')
    s = await view(page)
    expect(Number(s.today_copay)).toBe(300)
    await expect(cards(page)).toHaveCount(0)

    // 5. "הכי משתלם": cards, with the same numbers as the server and the chat
    const ranked = await say('מה הכי משתלם לי?')
    s = await view(page)
    expect(s.results.length).toBeGreaterThan(0)
    const summary = cards(page).last()                           // a ranking: one summary card, a row per option
    await expect(summary).toBeVisible({ timeout: 30_000 })
    for (const r of s.results.filter((x: { result: { status: string } }) => x.result.status === 'OK')) {
      const net = shekels(r.result.alternative.net_display)
      await expect(summary).toContainText(net)                   // card = server
      expect(ranked).toContain(net)                              // chat = server
      await expect(summary).toContainText(shekels(r.result.today.net_display))
      expect(Number(r.result.today.copay)).toBe(300)
    }
    const best = s.results.filter((x: { result: { status: string } }) => x.result.status === 'OK')
      .sort((a: { result: { delta_display: string } }, b: { result: { delta_display: string } }) =>
        Number(b.result.delta_display) - Number(a.result.delta_display))[0]
    expect(ranked).toContain(shekels(best.result.alternative.net_display))
    const todayBefore = Number(best.result.today.net_display)

    // 5b. "why" between the first two (stage 2): the engine's two nets and their difference, said by code; nothing
    // re-ranked, no unrelated question, the cards unchanged
    const ok = s.results.filter((x: { result: { status: string } }) => x.result.status === 'OK')
      .sort((a: { result: { delta_display: string } }, b: { result: { delta_display: string } }) =>
        Number(b.result.delta_display) - Number(a.result.delta_display))
    const [first, second] = ok
    const why = await say('למה דווקא הראשון?')
    const gap = Number(first.result.alternative.net_display) - Number(second.result.alternative.net_display)
    expect(why).toContain(shekels(first.result.alternative.net_display))
    expect(why).toContain(shekels(second.result.alternative.net_display))
    expect(why).toContain(`ההפרש ביניהם: ${shekels(gap)} ₪ בחודש`)
    expect(why).not.toContain('איזה רכבים להשוות')
    const unchanged = await view(page)
    expect(unchanged.results).toEqual(s.results)                 // a question: nothing recalculated or re-ranked

    // 6. a copay correction: saved, said once, recalculated
    const fixed = await say('רגע, ההשתתפות שלי היא 250 ולא 300')
    s = await view(page)
    expect(Number(s.today_copay)).toBe(250)
    expect(fixed.match(/250 ₪ במקום 300 ₪/g)?.length).toBe(1)
    expect(fixed.match(/עדכנתי/g)?.length).toBe(1)
    expect(s.results_stale).toBe(false)
    const after = s.results.find((x: { option_id: string }) => x.option_id === best.option_id)
    expect(Number(after.result.today.copay)).toBe(250)
    expect(Number(after.result.today.net_display)).toBe(todayBefore + 50)
    await expect(cards(page).last()).toContainText(shekels(after.result.today.net_display))   // the card updated
    for (const r of s.results.filter((x: { result: { status: string } }) => x.result.status === 'OK')) {
      expect(fixed).toContain(shekels(r.result.alternative.net_display))                       // chat = server
    }
  } finally {
    console.log(log.join('\n---\n'))
  }
})
