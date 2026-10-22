import { expect, test, type Locator, type Page } from '@playwright/test'

// The 360° map at its comparison limit of 3: every "להשוואה" button keeps its normal look and stays enabled;
// choosing a 4th adds nothing and says how to go on; a chosen option can always be removed, then another one
// chosen; the detailed comparison shows the 3 that are chosen. Mouse, keyboard and touch, desktop and phone.
// Template mode (no LLM); the server is the real one.

const BOT = '.bubble-bot .bubble-text'
const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const CAR = 'יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש"ח בחודש'
const LIMIT = 'ניתן להשוות עד 3 רכבים. כדי לבחור רכב אחר, בטלו תחילה אחת מהבחירות הקיימות.'

async function send(page: Page, text: string) {
  await expect(page.locator('.typing')).toHaveCount(0, { timeout: 45_000 })
  const bots = await page.locator(BOT).count()
  await page.getByLabel('הודעה').fill(text)
  await page.getByLabel('שליחה').click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
}

/** Up to the map, then open it (nothing is chosen yet). Returns the panel and its compare buttons. */
async function openMap(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
  await send(page, NOA)
  await send(page, CAR)
  const bots = await page.locator(BOT).count()
  await page.getByRole('button', { name: 'גלו לי את כל האפשרויות' }).click()
  await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
  await page.getByRole('button', { name: 'פתיחת מפת האפשרויות' }).click()
  const panel = page.getByRole('dialog', { name: 'מפת האפשרויות שלכם' })
  const buttons = panel.locator('.map-entry button')
  await expect(buttons.nth(3)).toBeAttached()
  return { panel, buttons }
}

const message = (panel: Locator) => panel.locator('.selection-limit')
const compare = (panel: Locator) => panel.locator('.panel-actions .chip-primary')
const label = (buttons: Locator, i: number) =>
  buttons.nth(i).locator('xpath=ancestor::li[contains(@class, "map-entry")]').locator('.map-name').innerText()

/** Looks like any other button: not faded, a normal pointer, enabled, and nothing covers its centre. */
async function looksNormal(button: Locator) {
  await button.scrollIntoViewIfNeeded()
  await expect(button).toBeEnabled()
  const look = await button.evaluate((el) => {
    const s = getComputedStyle(el)
    const b = el.getBoundingClientRect()
    const top = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)
    return { opacity: s.opacity, cursor: s.cursor, covered: !(top && el.contains(top)) }
  })
  expect(look).toEqual({ opacity: '1', cursor: 'pointer', covered: false })
}

/** The detailed comparison shows exactly the chosen options, and comparing chooses nothing in the conversation. */
async function compareAndCheck(page: Page, panel: Locator, labels: string[]) {
  await compare(panel).click()
  const table = page.getByRole('dialog', { name: 'טבלת השוואה' })
  await expect(table.locator('th.alt-col')).toHaveCount(3)
  for (const l of labels) await expect(table.locator('thead')).toContainText(l.split(' (')[0])
  await table.getByRole('button', { name: 'סגירה' }).click()
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  expect((await (await page.request.get(`/api/conversations/${id}`)).json()).selected).toEqual([])
}

test('mouse: 3 options, a 4th is explained and not added, swap one, compare', async ({ page }) => {
  const { panel, buttons } = await openMap(page)
  for (const i of [0, 1, 2]) {
    await buttons.nth(i).click()
    await expect(buttons.nth(i)).toHaveAttribute('aria-pressed', 'true')
  }
  await expect(compare(panel)).toHaveText('השוואה מפורטת (3)')
  await expect(message(panel)).toHaveCount(0)

  await looksNormal(buttons.nth(3))                                 // full, yet the 4th looks and works like any button
  await buttons.nth(3).click()
  await expect(message(panel)).toHaveText(LIMIT)
  await expect(message(panel)).toHaveAttribute('role', 'alert')
  await expect(message(panel)).toBeInViewport()
  await expect(buttons.nth(3)).toHaveAttribute('aria-pressed', 'false')
  await expect(compare(panel)).toHaveText('השוואה מפורטת (3)')
  for (const i of [0, 1, 2, 3]) await looksNormal(buttons.nth(i))   // the message hides none of them

  await buttons.nth(3).click()                                       // asked again: still nothing added
  await expect(message(panel)).toHaveText(LIMIT)
  await expect(buttons.nth(3)).toHaveAttribute('aria-pressed', 'false')

  await buttons.nth(1).click()                                       // a chosen option can always be removed
  await expect(buttons.nth(1)).toHaveAttribute('aria-pressed', 'false')
  await expect(compare(panel)).toHaveText('השוואה מפורטת (2)')
  await expect(message(panel)).toHaveCount(0)

  await buttons.nth(3).click()                                       // then another one can be chosen
  await expect(buttons.nth(3)).toHaveAttribute('aria-pressed', 'true')
  await expect(compare(panel)).toHaveText('השוואה מפורטת (3)')

  await compareAndCheck(page, panel, [await label(buttons, 0), await label(buttons, 2), await label(buttons, 3)])
})

test('keyboard: Space and Enter choose, the 4th is explained, focus stays, Shift+Tab back to remove one', async ({ page }) => {
  const { panel, buttons } = await openMap(page)
  await buttons.nth(0).focus()
  await page.keyboard.press('Space')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')
  await page.keyboard.press('Tab')
  await page.keyboard.press('Space')
  await expect(compare(panel)).toHaveText('השוואה מפורטת (3)')

  await page.keyboard.press('Tab')
  await expect(buttons.nth(3)).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(message(panel)).toHaveText(LIMIT)
  await expect(buttons.nth(3)).toHaveAttribute('aria-pressed', 'false')
  await expect(buttons.nth(3)).toBeFocused()                         // the refusal does not move the focus

  await page.keyboard.press('Shift+Tab')
  await expect(buttons.nth(2)).toBeFocused()
  await page.keyboard.press('Space')                                 // remove the 3rd
  await expect(buttons.nth(2)).toHaveAttribute('aria-pressed', 'false')
  await expect(message(panel)).toHaveCount(0)
  await page.keyboard.press('Tab')
  await page.keyboard.press('Enter')                                 // and choose the 4th
  await expect(buttons.nth(3)).toHaveAttribute('aria-pressed', 'true')
  await expect(compare(panel)).toHaveText('השוואה מפורטת (3)')
})

test('phone (touch, 375px): the same, with taps', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 375, height: 740 }, hasTouch: true, isMobile: true })
  const page = await context.newPage()
  const { panel, buttons } = await openMap(page)

  for (const i of [0, 1, 2]) {
    await buttons.nth(i).tap()
    await expect(buttons.nth(i)).toHaveAttribute('aria-pressed', 'true')
  }
  await looksNormal(buttons.nth(3))
  await buttons.nth(3).tap()
  await expect(message(panel)).toHaveText(LIMIT)
  await expect(message(panel)).toBeInViewport()
  await expect(buttons.nth(3)).toHaveAttribute('aria-pressed', 'false')

  await buttons.nth(0).tap()                                         // remove one
  await expect(message(panel)).toHaveCount(0)
  await buttons.nth(3).tap()
  await expect(buttons.nth(3)).toHaveAttribute('aria-pressed', 'true')
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)

  await compareAndCheck(page, panel, [await label(buttons, 1), await label(buttons, 2), await label(buttons, 3)])
  await context.close()
})
