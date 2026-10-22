import { expect, test, type APIRequestContext, type Page } from '@playwright/test'

// QA 22/10: after ~7 actions the rep portal said "יותר מדי בקשות. נסו שוב בעוד דקה.". The screen polled both
// lists every 3 s (40 requests a minute against a limit of 60) and every answer added 3. Now it polls the list
// on screen every 5 s and the server keeps room for answers (api/ratelimit.RepGate).

const REP_TOKEN = process.env.REP_TOKEN || ''
const REP = { 'X-Rep-Token': REP_TOKEN }
const NOA = 'מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const CAR = 'יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש"ח בחודש'
const TOO_MANY = 'יותר מדי בקשות'

/** One employee conversation that ends waiting for the rep's approval of ATTO 3's copay. */
async function waitingForRep(request: APIRequestContext) {
  const cid = (await (await request.post('/api/conversations')).json()).conversation_id
  for (const text of [NOA, CAR, 'אטו 3']) {
    const r = await request.post(`/api/conversations/${cid}/messages`, { data: { text } })
    expect(r.ok()).toBeTruthy()
  }
}

/** Every 429 the rep API returned to this page, from the moment it opens. */
function refusals(page: Page): number[] {
  const seen: number[] = []
  page.on('response', (r) => { if (r.url().includes('/api/rep/') && r.status() === 429) seen.push(r.status()) })
  return seen
}

async function openRep(page: Page) {
  await page.addInitScript((tok) => sessionStorage.setItem('sca.repToken', tok), REP_TOKEN)
  await page.goto('/rep')
  await expect(page.getByRole('heading', { name: 'פורטל נציגי תחבורה' })).toBeVisible({ timeout: 20_000 })
}

test.describe('the rep portal is never blocked by its own polling', () => {
  test.skip(!REP_TOKEN, 'REP_TOKEN not provided')

  test('ten answers in a row with the screen open, then the answered list after a tab switch and a reload',
       async ({ page, request }) => {
    test.setTimeout(240_000)                      // a full minute of a rep's screen, by design
    for (let i = 0; i < 10; i++) await waitingForRep(request)
    const before = (await (await request.get('/api/rep/questions?status=ANSWERED', { headers: REP })).json()).length

    const refused = refusals(page)
    await openRep(page)
    const confirm = page.locator('.rep-card').getByRole('button', { name: /^אישור/ })
    await expect(confirm.first()).toBeVisible({ timeout: 20_000 })
    // the rep had the screen open for a minute before answering (the QA report): before the fix its own polling
    // had used up 40 of the 60 by now, and the answers' requests went past the limit
    await page.waitForTimeout(60_000)
    for (let i = 0; i < 10; i++) {
      const left = await confirm.count()
      await confirm.first().click()
      await expect(page.getByRole('status').filter({ hasText: 'התשובה נשלחה לעובד' })).toBeVisible()
      await expect.poll(async () => confirm.count(), { timeout: 15_000 }).toBeLessThan(left)
      await expect(page.getByText(TOO_MANY)).toHaveCount(0)
      await expect(page.locator('.rep-paused')).toHaveCount(0)
    }
    const after = (await (await request.get('/api/rep/questions?status=ANSWERED', { headers: REP })).json()).length
    expect(after - before).toBe(10)

    // the answered list is current when its tab opens, and after a reload
    await page.getByRole('tab', { name: 'נענו' }).click()
    await expect.poll(async () => page.locator('.rep-card').count(), { timeout: 10_000 }).toBeGreaterThanOrEqual(10)
    await page.reload()
    await expect(page.locator('.rep-stats dd').nth(1)).not.toHaveText('—', { timeout: 20_000 })
    await page.getByRole('tab', { name: 'נענו' }).click()
    await expect.poll(async () => page.locator('.rep-card').count(), { timeout: 10_000 }).toBeGreaterThanOrEqual(10)
    await expect(page.getByText(TOO_MANY)).toHaveCount(0)
    expect(refused).toEqual([])                                  // not one 429 from start to end
  })

  test('an idle screen asks little: about one list every 5 seconds', async ({ page }) => {
    const seen: string[] = []
    page.on('request', (r) => { if (r.url().includes('/api/rep/questions')) seen.push(r.url()) })
    await openRep(page)
    await page.waitForTimeout(500)
    const start = seen.length                                    // the first load: both lists
    await page.waitForTimeout(21_000)
    const polls = seen.length - start
    expect(polls).toBeGreaterThanOrEqual(3)                      // it does keep the list fresh
    expect(polls).toBeLessThanOrEqual(5)                         // ~4 in 20 s; it was 14 (two lists every 3 s)
  })

  test('two reps on one network answer ten each: every answer is saved, none refused', async ({ browser, request }) => {
    test.setTimeout(240_000)                      // a full minute of a rep's screen, by design
    // a venue's Wi-Fi: two browsers, one address. Each screen has its own session (X-Rep-Session), so each rep
    // has their own answer budget; before, the second rep's answers were refused once the first had used it
    for (let i = 0; i < 20; i++) await waitingForRep(request)
    const before = (await (await request.get('/api/rep/questions?status=ANSWERED', { headers: REP })).json()).length
    const reps = [await browser.newContext(), await browser.newContext()]
    const pages = [await reps[0].newPage(), await reps[1].newPage()]
    const refused = pages.map(refusals)
    for (const p of pages) await openRep(p)
    for (let i = 0; i < 10; i++) {
      for (const [n, p] of pages.entries()) {
        const confirm = p.locator('.rep-card').getByRole('button', { name: /^אישור/ })
        await expect(confirm.first()).toBeVisible({ timeout: 15_000 })
        const card = n === 0 ? confirm.first() : confirm.last()    // the two reps work from opposite ends
        const responded = p.waitForResponse((r) => r.url().includes('/answer') && r.request().method() === 'POST')
        await card.click()
        const r = await responded
        expect([200, 409]).toContain(r.status())       // 409: the other rep answered this card a moment earlier
      }
    }
    expect(refused.flat()).toEqual([])
    const after = (await (await request.get('/api/rep/questions?status=ANSWERED', { headers: REP })).json()).length
    expect(after - before).toBeGreaterThanOrEqual(18)    // ~20; a 409 is a card both reps reached, saved once
    for (const ctx of reps) await ctx.close()
  })

  test('three tabs open together are not blocked', async ({ browser }) => {
    test.setTimeout(240_000)                      // a full minute of a rep's screen, by design
    const ctx = await browser.newContext()
    const pages = [await ctx.newPage(), await ctx.newPage(), await ctx.newPage()]
    const refused = pages.map(refusals)
    for (const p of pages) await openRep(p)
    await pages[0].waitForTimeout(65_000)                        // a full window: before the fix 3 x 40 > 60
    for (const p of pages) {
      await expect(p.getByText(TOO_MANY)).toHaveCount(0)
      await expect(p.locator('.rep-paused')).toHaveCount(0)
    }
    expect(refused.flat()).toEqual([])                           // not one 429 in any tab, the whole minute
    await ctx.close()                // the rep screens poll: closed so the next tests start from a quiet address
  })
})
