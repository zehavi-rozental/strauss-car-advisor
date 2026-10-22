/// <reference types="node" />
// The real PDF: pdf-lib + fontkit + the Assistant font files from node_modules, as in the browser.
// REPORT_SAMPLE_DIR=<folder> npx vitest run src/employee/report  also writes the sample PDFs there.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { PDFDocument } from 'pdf-lib'
import { describe, expect, it } from 'vitest'
import type { OptionResult } from '../../api/types'
import { formatMoney } from '../../format/money'
import { MANY, THREE, item } from './fixtures.test-data'
import { REPORT_TITLE, renderReportPdf } from './pdfReport'
import type { RenderedReport } from './pdfReport'
import { buildReport } from './reportModel'

const font = (name: string) => readFileSync(resolve(process.cwd(), `node_modules/@fontsource/assistant/files/assistant-${name}-normal.woff`))
const FONTS = { hebrew: font('hebrew-400'), latin: font('latin-400'), hebrewBold: font('hebrew-700'), latinBold: font('latin-700') }
const AT = new Date(2026, 9, 22, 14, 32)
const PAGE_W = 595.28

async function render(results: OptionResult[], sample?: string): Promise<RenderedReport> {
  const out = await renderReportPdf(buildReport(results, AT), FONTS)
  const dir = process.env.REPORT_SAMPLE_DIR
  if (dir && sample) { mkdirSync(dir, { recursive: true }); writeFileSync(join(dir, sample), out.bytes) }
  return out
}
const said = (r: RenderedReport) => r.ops.map((o) => o.text).join('\n')

describe('the PDF report', () => {
  it('is a valid A4 PDF with its title, language and the options in its metadata', async () => {
    const r = await render(THREE, 'sample-three-options.pdf')
    expect(Buffer.from(r.bytes.slice(0, 5)).toString()).toBe('%PDF-')
    const doc = await PDFDocument.load(r.bytes)
    expect(doc.getPageCount()).toBe(r.pages)
    expect(doc.getPage(0).getSize()).toEqual({ width: PAGE_W, height: 841.89 })
    expect(doc.getTitle()).toBe(REPORT_TITLE)
    for (const o of THREE) expect(doc.getSubject()).toContain(o.label)
  })

  it('writes every option and every server amount, unchanged', async () => {
    const r = await render(THREE)
    const text = said(r)
    expect(text).toContain(formatMoney('11032'))                               // today
    for (const o of THREE) {
      expect(text).toContain(o.label)
      expect(text).toContain(formatMoney(o.result.alternative!.net_display))
      expect(text).toContain(`${formatMoney(o.result.delta_display!, { signed: true })} לעומת היום`)
      expect(text).toContain(`בשנה ${formatMoney(Number(o.result.delta_display) * 12, { signed: true })}`)
    }
    expect(text).toContain('הנטו החודשי הגבוה ביותר')
    expect(text).toContain('ממתינה לאישור נציג')
    expect(text).not.toMatch(/NaN|undefined|null|Infinity/)
  })

  it('shows a car without a result as such, never as 0 ₪', async () => {
    const r = await render([item('A', 'A (2025)', 12000), item('X', 'X LONG (2026)', null)])
    const card = r.ops.filter((o) => o.text === 'אין חישוב לחלופה הזו')
    expect(card).toHaveLength(1)
    const doc = await PDFDocument.load(r.bytes)
    expect(doc.getKeywords()).toContain('X LONG (2026): אין חישוב')
  })

  it('carries more than 3 options, a waiver, a loss and a long name onto more pages, inside the margins', async () => {
    const r = await render(MANY, 'sample-six-options.pdf')
    const text = said(r)
    const subject = (await PDFDocument.load(r.bytes)).getSubject()
    for (const o of MANY) {
      expect(subject).toContain(o.label)                                       // whole, in the metadata
      for (const word of o.label.split(' ')) expect(text).toContain(word)      // a long name wraps on the page
    }
    expect(text).toContain('ויתור על הרכב')
    expect(text).toContain(formatMoney(10109 - 11032, { signed: true }))       // the loss, with its minus
    expect(text).toContain('פירוט החישוב (1 מתוך 2)')
    expect(text).toContain('פירוט החישוב (2 מתוך 2)')
    expect(r.pages).toBeGreaterThanOrEqual(2)
    expect(text).toContain(`עמוד 1 מתוך ${r.pages}`)
    for (const op of r.ops) {
      expect(op.x).toBeGreaterThanOrEqual(39.5)
      expect(op.x + op.width).toBeLessThanOrEqual(PAGE_W - 39.5)
      expect(op.y).toBeGreaterThanOrEqual(30)
    }
  })

  it('draws Hebrew right to left and car names left to right', async () => {
    const r = await render(THREE)
    const title = r.ops.find((o) => o.text === REPORT_TITLE)!
    expect(title.visual).toBe(Array.from(REPORT_TITLE).reverse().join(''))
    const atto = r.ops.find((o) => o.text.includes('ATTO 3 (2025)'))!
    expect(atto.visual).toContain('ATTO 3 (2025)')
  })

  it('follows a new calculation', async () => {
    const before = await render(THREE)
    const after = await render([item('CAR-10', 'ATTO 3 (2025)', 11688), ...THREE.slice(1)])
    expect(said(before)).toContain(formatMoney('11693'))
    expect(said(after)).toContain(formatMoney('11688'))
    expect(said(after)).not.toContain(formatMoney('11693'))
  })
})
