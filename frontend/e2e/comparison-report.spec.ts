import { expect, test, type Page } from '@playwright/test'
import { readFileSync } from 'node:fs'
import { PDFDocument } from 'pdf-lib'
import { formatMoney } from '../src/format/money'

// "הורדת דוח השוואה" through the real screen and server: the button appears only with a result, the download is a
// real PDF, and the report holds exactly this conversation's results, with the server's numbers (checked against
// GET /api/conversations/{id}). The PDF's subject lists the options and its keywords hold each option's net, so
// the file itself can be checked here. Template mode (no LLM).

const BOT = '.bubble-bot .bubble-text'
const USER = '.bubble-user'
const NOA = 'אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים'
const BUTTON = 'הורדת דוח השוואה'
const TITLE = 'דוח השוואת חלופות רכב'

async function open(page: Page) {
  await page.goto('/employee')
  await expect(page.locator(BOT).first()).toBeVisible({ timeout: 30_000 })
}

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

interface Side { label: string; net_display: string }
interface View {
  conversation_id: string
  results: { option_id: string; label: string; result: { today: Side | null; alternative: Side | null } }[]
}
async function state(page: Page): Promise<View> {
  const id = await page.evaluate(() => localStorage.getItem('sca.conversationId'))
  const r = await page.request.get(`/api/conversations/${id}`)
  expect(r.status()).toBe(200)
  return r.json()
}

/** Click (or tap) a report button, save the file, and read it back. */
async function download(page: Page, where: 'card' | 'panel' = 'card', tap = false) {
  const scope = where === 'panel' ? page.locator('.comparison-panel') : page.locator('.result-cards')
  const button = scope.getByRole('button', { name: BUTTON })
  await expect(button).toBeVisible()
  const [file] = await Promise.all([page.waitForEvent('download'), tap ? button.tap() : button.click()])
  expect(file.suggestedFilename()).toMatch(/^STRAUSS-DRIVE-comparison-\d{4}-\d{2}-\d{2}-\d{4}\.pdf$/)
  const bytes = readFileSync((await file.path())!)
  expect(bytes.subarray(0, 5).toString()).toBe('%PDF-')
  const doc = await PDFDocument.load(bytes)
  expect(doc.getPageCount()).toBeGreaterThanOrEqual(1)
  expect(doc.getTitle()).toBe(TITLE)
  return { subject: doc.getSubject() ?? '', keywords: doc.getKeywords() ?? '' }
}

/** The PDF names exactly this conversation's options, each with the server's net, and today's net. */
function expectMatches(pdf: { subject: string; keywords: string }, view: View) {
  const labels = view.results.map((r) => r.label)
  expect(pdf.subject.replace('השוואת חלופות רכב: ', '').split(', ').sort()).toEqual([...labels].sort())
  for (const r of view.results) {
    expect(pdf.keywords).toContain(`${r.label}: ${r.result.alternative ? formatMoney(r.result.alternative.net_display) : 'אין חישוב'}`)
  }
  const today = view.results.find((r) => r.result.today)?.result.today
  if (today) expect(pdf.keywords).toContain(`היום ${today.label}: ${formatMoney(today.net_display)}`)
}

async function compareThree(page: Page) {
  for (const m of [NOA, 'יש לי קורולה קרוס', '300']) await send(page, m)
  await page.getByRole('button', { name: 'להציג את הרכבים בקבוצה שלכם' }).click()
  const cards = page.locator('.car-card:not(.car-card-optout)')
  for (const i of [0, 1, 2]) await cards.nth(i).click()
  await page.getByRole('button', { name: 'השוואה' }).click()
  await expect(page.locator('.summary-card')).toBeVisible({ timeout: 45_000 })
}

test('no report before a result; one result: the card offers it and the PDF holds the server numbers', async ({ page }) => {
  await open(page)
  await expect(page.getByRole('button', { name: BUTTON })).toHaveCount(0)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300']) await send(page, m)
  await expect(page.getByRole('button', { name: BUTTON })).toHaveCount(0)        // the question, not a result yet
  await send(page, 'אטו 3')
  await expect(page.locator('.result-card')).toBeVisible()
  const view = await state(page)
  expect(view.results).toHaveLength(1)
  expectMatches(await download(page), view)
})

test('three options: the card and the comparison window, a second download, and the numbers follow a correction', async ({ page }) => {
  await open(page)
  await compareThree(page)
  const before = await state(page)
  expect(before.results).toHaveLength(3)
  const first = await download(page)
  expectMatches(first, before)
  expectMatches(await download(page), before)                                    // downloading again gives the same

  await page.locator('.result-cards').getByRole('button', { name: 'פירוט מלא' }).click()
  expectMatches(await download(page, 'panel'), before)
  await page.getByRole('button', { name: 'סגירה' }).first().click()

  await send(page, 'טעיתי, השכר שלי 16,000')                                     // the server recalculates everything
  await expect.poll(async () => JSON.stringify((await state(page)).results.map((r) => r.result.alternative?.net_display)))
    .not.toBe(JSON.stringify(before.results.map((r) => r.result.alternative?.net_display)))
  const after = await state(page)
  const second = await download(page)
  expectMatches(second, after)
  expect(second.keywords).not.toBe(first.keywords)
})

test('opting out of the car is reported as such', async ({ page }) => {
  await open(page)
  await send(page, 'אני מנהלת פיתוח, שכר בסיס 27,000, אין עמלות, אין בונוס, פנסיה 7%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, ילד אחד')
  for (const m of ['מאזדה 3', '350', 'ויתור על הרכב']) await send(page, m)
  const view = await state(page)
  expect(view.results.map((r) => r.option_id)).toEqual(['OPT_OUT'])
  const pdf = await download(page)
  expectMatches(pdf, view)
  expect(pdf.subject).toContain(view.results[0].label)
})

test("each employee's report holds only their own conversation", async ({ browser }) => {
  const a = await browser.newContext()
  const b = await browser.newContext()
  const pa = await a.newPage()
  const pb = await b.newPage()
  await open(pa)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(pa, m)
  await open(pb)
  for (const m of [NOA.replace('18,500', '25,000'), 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(pb, m)
  const [va, vb] = [await state(pa), await state(pb)]
  expect(va.conversation_id).not.toBe(vb.conversation_id)
  const [ra, rb] = [await download(pa), await download(pb)]
  expectMatches(ra, va)
  expectMatches(rb, vb)
  const netA = formatMoney(va.results[0].result.alternative!.net_display)
  const netB = formatMoney(vb.results[0].result.alternative!.net_display)
  expect(netA).not.toBe(netB)
  expect(ra.keywords).not.toContain(netB)
  expect(rb.keywords).not.toContain(netA)
  await a.close()
  await b.close()
})

test('on a phone, after a reload, the report still downloads with a tap', async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 375, height: 740 }, hasTouch: true, isMobile: true })
  const page = await context.newPage()
  await open(page)
  for (const m of [NOA, 'יש לי קורולה קרוס', '300', 'אטו 3']) await send(page, m)
  await page.reload()
  await expect(page.locator('.result-card')).toBeVisible({ timeout: 30_000 })
  expectMatches(await download(page, 'card', true), await state(page))
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await context.close()
})
