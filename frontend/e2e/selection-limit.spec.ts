import { expect, test, type Locator, type Page } from '@playwright/test'

// The selection card at its limit of 3: every card keeps its normal look and stays enabled; choosing a 4th adds
// nothing and says how to go on; a selected card can always be removed, then another car can be chosen; the
// comparison runs on the 3 that are selected. Mouse, keyboard and touch, on a desktop and a phone.
// Template mode (no LLM); the server is the real one.

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const LIMIT = 'ניתן להשוות עד 3 רכבים. כדי לבחור רכב אחר, בטלו תחילה אחת מהבחירות הקיימות.'

async function send(page: Page, text: string) {
  await expect(page.locator('.typing')).toHaveCount(0, { timeout: 45_000 })
  const users = await page.locator(USER).count()
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await expect(page.getByLabel('שליחה')).toBeEnabled({ timeout: 45_000 })
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(USER).count(), { timeout: 45_000 }).toBeGreaterThan(users)
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

/** Up to the alternatives question, then open the cards from its link (nothing is selected yet). */
async function openCards(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  for (const m of [NOA, 'יש לי קורולה קרוס', '300']) await send(page, m)
  await page.getByRole('button', { name: 'להציג את הרכבים בקבוצה שלכם' }).click()
  const cards = page.locator('.car-card:not(.car-card-optout)')
  await expect(cards.nth(3)).toBeVisible()
  return cards
}

const counter = (page: Page) => page.locator('.selection-counter')
const message = (page: Page) => page.locator('.selection-limit')

/** Looks like any other card: not faded, a normal pointer, enabled, and nothing covers its centre. */
async function looksNormal(card: Locator) {
  await expect(card).toBeEnabled()
  const look = await card.evaluate((el) => {
    const s = getComputedStyle(el)
    const b = el.getBoundingClientRect()
    const top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)
    return { opacity: s.opacity, cursor: s.cursor, covered: !(top && el.contains(top)) }
  })
  expect(look).toEqual({ opacity: '1', cursor: 'pointer', covered: false })
}

async function compareAndCheck(page: Page, names: string[]) {
  await page.getByRole('button', { name: 'השוואה' }).click()
  await expect(page.locator('.summary-card')).toBeVisible({ timeout: 45_000 })
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const view = await (await page.request.get(`/api/conversations/${id}`)).json()
  const labels: string[] = view.results.map((r: { label: string }) => r.label)
  expect(labels).toHaveLength(3)
  for (const n of names) expect(labels.some((l) => l.startsWith(n))).toBe(true)
}

test('mouse: 3 cars, a 4th is explained and not added, swap one, compare', async ({ page }) => {
  const cards = await openCards(page)
  const name = (i: number) => cards.nth(i).locator('.car-name').innerText()

  for (const i of [0, 1, 2]) {
    await cards.nth(i).click()
    await expect(cards.nth(i)).toHaveAttribute('aria-pressed', 'true')
    await expect(counter(page)).toContainText(`נבחרו ${i + 1} מתוך 3`)
  }
  await expect(message(page)).toHaveCount(0)

  await looksNormal(cards.nth(3))                                   // full, yet the 4th looks and works like any card
  await cards.nth(3).click()
  await expect(message(page)).toHaveText(LIMIT)
  await expect(message(page)).toHaveAttribute('role', 'alert')
  await expect(cards.nth(3)).toHaveAttribute('aria-pressed', 'false')
  await expect(counter(page)).toContainText('נבחרו 3 מתוך 3')
  for (const i of [0, 1, 2, 3]) await looksNormal(cards.nth(i))     // the message hides none of the cards

  await cards.nth(3).click()                                         // asked again: still nothing added
  await expect(message(page)).toHaveText(LIMIT)
  await expect(cards.nth(3)).toHaveAttribute('aria-pressed', 'false')

  await cards.nth(1).click()                                         // a selected car can always be removed
  await expect(cards.nth(1)).toHaveAttribute('aria-pressed', 'false')
  await expect(counter(page)).toContainText('נבחרו 2 מתוך 3')
  await expect(message(page)).toHaveCount(0)

  await cards.nth(3).click()                                         // then another car can be chosen
  await expect(cards.nth(3)).toHaveAttribute('aria-pressed', 'true')
  await expect(counter(page)).toContainText('נבחרו 3 מתוך 3')

  await compareAndCheck(page, [await name(0), await name(2), await name(3)])
})

test('keyboard: Space and Enter choose, the 4th is explained, focus stays, Shift+Tab back to remove one', async ({ page }) => {
  const cards = await openCards(page)
  await cards.nth(0).focus()
  await page.keyboard.press('Space')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Space')
  await expect(counter(page)).toContainText('נבחרו 3 מתוך 3')

  await page.keyboard.press('Tab')
  await expect(cards.nth(3)).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(message(page)).toHaveText(LIMIT)
  await expect(cards.nth(3)).toHaveAttribute('aria-pressed', 'false')
  await expect(cards.nth(3)).toBeFocused()                          // the refusal does not move the focus

  await page.keyboard.press('Shift+Tab')
  await expect(cards.nth(2)).toBeFocused()
  await page.keyboard.press('Space')                                 // remove the 3rd
  await expect(cards.nth(2)).toHaveAttribute('aria-pressed', 'false')
  await expect(message(page)).toHaveCount(0)
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')                                 // and choose the 4th
  await expect(cards.nth(3)).toHaveAttribute('aria-pressed', 'true')
  await expect(counter(page)).toContainText('נבחרו 3 מתוך 3')
})

test('phone (touch, 375px): the same, with taps', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 375, height: 740 }, hasTouch: true, isMobile: true })
  const page = await context.newPage()
  const cards = await openCards(page)
  const name = (i: number) => cards.nth(i).locator('.car-name').innerText()

  for (const i of [0, 1, 2]) {
    await cards.nth(i).tap()
    await expect(cards.nth(i)).toHaveAttribute('aria-pressed', 'true')
  }
  await cards.nth(3).scrollIntoViewIfNeeded()
  await looksNormal(cards.nth(3))
  await cards.nth(3).tap()
  await expect(message(page)).toHaveText(LIMIT)
  await expect(message(page)).toBeInViewport()
  await expect(cards.nth(3)).toHaveAttribute('aria-pressed', 'false')

  await cards.nth(0).scrollIntoViewIfNeeded()
  await cards.nth(0).tap()                                           // remove one
  await expect(message(page)).toHaveCount(0)
  await cards.nth(3).scrollIntoViewIfNeeded()
  await cards.nth(3).tap()
  await expect(cards.nth(3)).toHaveAttribute('aria-pressed', 'true')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)

  await compareAndCheck(page, [await name(1), await name(2), await name(3)])
  await context.close()
})
