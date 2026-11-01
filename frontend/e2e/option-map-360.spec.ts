import { expect, test, type Page } from '@playwright/test'

// STRAUSS DRIVE 360° in the browser, against the real server WITHOUT an LLM (template mode): every eligible
// option, the engine's numbers, statuses, the detailed comparison, a reload and a phone-width screen.

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const CAR = 'יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש"ח בחודש'

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

async function state(page: Page) {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  return (await page.request.get(`/api/conversations/${id}`)).json()
}

for (const viewport of [{ width: 1280, height: 800 }, { width: 375, height: 740 }]) {
  test(`one button maps every eligible option, with the engine's numbers (${viewport.width}px)`, async ({ browser }) => {
    const context = await browser.newContext({ viewport })
    const page = await context.newPage()
    await open(page)
    await send(page, NOA)
    await send(page, CAR)
    const bots = await page.locator(BOT).count()
    await page.getByRole('button', { name: 'כל הרכבים לפי נטו' }).click()
    await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
    await expect(page.locator(BOT).last()).toContainText('מיפיתי את כל 13 החלופות')
    await expect(page.locator(BOT).last()).toContainText('הדירוג מבוסס על הנטו החודשי המשוער בלבד')

    const s = await state(page)
    expect(s.selected).toEqual([])                                     // nothing chosen for the employee
    expect(s.option_map.entries).toHaveLength(13)

    await page.getByRole('button', { name: 'פתיחת מפת האפשרויות' }).click()
    const panel = page.getByRole('dialog', { name: 'מפת האפשרויות שלכם' })
    await expect(panel.locator('.map-entry')).toHaveCount(13)          // every option, not only three
    await expect(panel.locator('.map-lead')).toHaveCount(s.option_map.leaders.length)
    const first = s.option_map.entries[0]
    const card = panel.locator(`.map-entry[data-option="${first.option_id}"]`)
    await expect(card).toContainText(first.label)
    await expect(card.locator('.money').first()).toContainText(
      Number(first.result.alternative.net_display).toLocaleString('en-US'))
    await expect(panel.locator('img')).toHaveCount(0)                   // information, no car pictures

    await panel.locator('.map-entry').nth(1).getByRole('button', { name: 'להשוואה' }).click()
    await panel.locator('.map-entry').nth(2).getByRole('button', { name: 'להשוואה' }).click()
    await panel.getByRole('button', { name: 'השוואה לעיון (2)' }).click()
    const table = page.getByRole('dialog', { name: 'טבלת השוואה' })
    await expect(table.locator('th.alt-col')).toHaveCount(2)
    await expect(table).toContainText("השוואה לעיון בלבד. ההשוואה בצ'אט לא משתנה.")
    await expect(table.getByRole('button', { name: 'לשנות בחירה' })).toHaveCount(0)   // nothing here changes the chat
    await table.getByRole('button', { name: 'סגירה' }).click()
    expect((await state(page)).selected).toEqual([])                   // comparing is not choosing

    // only the explicit button moves the browsed cars into the chat's comparison (the same selection path)
    const picked = [s.option_map.entries[1].option_id, s.option_map.entries[2].option_id].sort()
    await panel.locator('.map-entry').nth(1).getByRole('button', { name: 'להשוואה' }).click()
    await panel.locator('.map-entry').nth(2).getByRole('button', { name: 'להשוואה' }).click()
    await panel.getByRole('button', { name: 'השוואה לעיון (2)' }).click()
    const moveBots = await page.locator(BOT).count()
    await table.getByRole('button', { name: "להעביר להשוואה בצ'אט" }).click()
    await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(moveBots)
    await expect(page.getByRole('dialog')).toHaveCount(0)              // both panels closed, back in the chat
    expect([...(await state(page)).selected].sort()).toEqual(picked)
    await expect(page.locator('.car-actions-current')).toContainText("בהשוואה בצ'אט")

    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
    expect(overflow).toBeLessThanOrEqual(0)
    await context.close()
  })
}

test('the map asked in words survives a reload, and a salary change rebuilds it', async ({ page }) => {
  await open(page)
  await send(page, 'השווה את כל האפשרויות שלי')
  await expect(page.locator(BOT).last()).toContainText('כדי למפות את כל האפשרויות, חסרים לי עוד')
  await send(page, NOA)
  await send(page, CAR)
  await expect(page.locator(BOT).last()).toContainText('מיפיתי את כל 13 החלופות')     // ready: shown at once
  const before = (await state(page)).option_map
  await page.reload()
  await expect(page.getByRole('button', { name: 'פתיחת מפת האפשרויות' })).toBeVisible({ timeout: 30_000 })
  expect((await state(page)).option_map).toEqual(before)
  await send(page, 'טעיתי, השכר שלי 25,000')
  await expect(page.locator(BOT).last()).toContainText('עדכנתי את מפת האפשרויות')
  const after = (await state(page)).option_map
  expect(after.stale).toBe(false)
  expect(after.entries[0].result.today.net_display).not.toBe(before.entries[0].result.today.net_display)
  await expect(page.locator(USER).last()).toContainText('25,000')
})

test('a car correction marks the map out of date until the new model is known', async ({ page }) => {
  await open(page)
  for (const m of [NOA, CAR, 'השווה את כל האפשרויות שלי', 'התכוונתי לרכב אחר']) await send(page, m)
  await expect(page.locator('.map-card .stale-note')).toContainText('לא מעודכן')
  await page.getByRole('button', { name: 'פתיחת מפת האפשרויות' }).click()
  const panel = page.getByRole('dialog', { name: 'מפת האפשרויות שלכם' })
  await expect(panel.locator('.map-lead')).toHaveCount(0)              // no leader from an out-of-date map
  await panel.getByRole('button', { name: 'סגירה' }).click()
  await send(page, 'קורולה 2026')
  await expect(page.locator('.map-card .stale-note')).toHaveCount(0)
})

// QA 22/10: the button showed at the car question (before the salary leftovers and today's car were known) and
// a click only got "חסרים לי עוד". It is offered only when the server says the map can be built (map_ready).
for (const viewport of [{ width: 1280, height: 800 }, { width: 375, height: 740 }]) {
  test(`the map button appears only when the map can really be shown (${viewport.width}px)`, async ({ browser }) => {
    const page = await (await browser.newContext({ viewport })).newPage()
    const button = page.getByRole('button', { name: 'כל הרכבים לפי נטו' })
    await open(page)
    await expect(button).toHaveCount(0)                                       // the greeting
    for (const m of ['מנהלת מוצר, שכר בסיס 18,500', 'אין עמלות, בונוס 2,000, פנסיה 6%']) {
      await send(page, m)
      await expect(button).toHaveCount(0)                                     // the screenshot's car question
    }
    expect((await state(page)).map_ready).toBe(false)
    await send(page, 'קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים')
    await expect(button).toHaveCount(0)                                       // today's car still missing
    await send(page, CAR)
    await expect(button).toBeVisible({ timeout: 15_000 })                     // everything known: offered
    const bots = await page.locator(BOT).count()
    await button.click()
    await expect.poll(async () => page.locator(BOT).count(), { timeout: 45_000 }).toBeGreaterThan(bots)
    await expect(page.locator(BOT).last()).toContainText('מיפיתי את כל')        // the promise is kept
    await expect(page.locator(BOT).last()).not.toContainText('חסרים לי עוד')
    await button.click()                                                      // the same entry opens the map now
    await expect(page.getByRole('dialog', { name: 'מפת האפשרויות שלכם' })).toBeVisible()
    await page.context().close()
  })
}

// Live QA 1/11: after the map, "אני רוצה להחליף נתון…" was offered to the representative and the map's card was drawn
// again under the new reply. A correction is asked in code and saved like the form; the card stays at its message
// (out of date while the data changes) and moves only to the message that rebuilt it, at both widths and on reload.
for (const viewport of [{ width: 1280, height: 800 }, { width: 375, height: 740 }]) {
  test(`a correction after the map: asked, saved, recalculated; the map card stays in its place (${viewport.width}px)`,
       async ({ browser }) => {
    const page = await (await browser.newContext({ viewport })).newPage()
    await open(page)
    for (const m of [NOA, CAR, 'השווה את כל האפשרויות שלי']) await send(page, m)
    const card = page.locator('.map-card')
    await expect(card).toHaveCount(1)
    const mapMessage = await card.evaluate((el) => el.closest('.message-group')?.id)
    expect(mapMessage).toBeTruthy()
    const before = (await state(page)).option_map

    await send(page, 'אני רוצה להחליף נתון אחד שלא כתבתי נכון מה לעשות?')
    const reply = page.locator(BOT).last()
    await expect(reply).toContainText('מה תרצו לתקן?')
    await expect(reply).not.toContainText('נציג')
    await expect(page.getByRole('button', { name: 'לשאול את נציג הרכב' })).toHaveCount(0)
    await expect(card).toHaveCount(1)
    expect(await card.evaluate((el) => el.closest('.message-group')?.id)).toBe(mapMessage)   // not re-drawn below

    await page.getByRole('button', { name: 'לפתוח את טופס תיקון הנתונים' }).click()
    await expect(page.getByRole('form', { name: 'תיקון נתונים' })).toBeVisible()
    await page.getByRole('button', { name: 'ביטול' }).click()

    await send(page, 'השכר שהכנסתי לא נכון')
    await expect(page.locator(BOT).last()).toContainText('מה הערך הנכון של שכר בסיס?')
    await page.reload()                                                   // mid-correction: the question stays
    await expect(page.locator(BOT).last()).toContainText('מה הערך הנכון של שכר בסיס?', { timeout: 30_000 })
    await expect(card).toHaveCount(1)
    expect(await card.evaluate((el) => el.closest('.message-group')?.id)).toBe(mapMessage)   // back in its place
    await send(page, '25000')
    await expect(page.locator(BOT).last()).toContainText('עדכנתי: שכר בסיס 25,000 ₪ במקום 18,500 ₪')
    const after = (await state(page)).option_map
    expect(after.stale).toBe(false)
    expect(after.entries[0].result.today.net_display).not.toBe(before.entries[0].result.today.net_display)
    await expect(card).toHaveCount(1)
    expect(await card.evaluate((el) => el.closest('.message-group')?.id)).not.toBe(mapMessage)   // the rebuild's message
    expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0)
    await page.context().close()
  })
}
