// The comparison report as a real PDF (A4, selectable text, embedded Assistant font), drawn in the browser with
// pdf-lib from a ReportModel. Nothing is sent anywhere and nothing is calculated here: every amount is the model's
// (that is, the server's) value, formatted with the same formatMoney the screen uses.
// Layout: a blue header band, today's card, one card per option (the highest monthly net in Strauss blue), the
// breakdown table (the comparison screen's rows; more than 3 options continue in another table), then the notes.
// Every text line is recorded in `ops` so the tests can check what was written and where.
import { PDFDocument, ReadingDirection, degrees, rgb } from 'pdf-lib'
import type { PDFFont, PDFPage } from 'pdf-lib'
import fontkit from '@pdf-lib/fontkit'
import type { RGB } from '../../art/palette'
import { DEFAULT_REPORT_THEME } from '../../art/reportTheme'
import type { ReportColour, ReportTheme } from '../../art/reportTheme'
import { formatMoney } from '../../format/money'
import { PETALS, petal } from '../../ui/brandMarkShape'
import type { Cell, Row } from '../comparison'
import { fontRuns, glyphOrder, ltr, plain, visualOrder } from './bidi'
import { generatedText } from './reportModel'
import type { ReportModel, ReportOption } from './reportModel'

export interface ReportFonts {
  hebrew: Uint8Array | ArrayBuffer
  latin: Uint8Array | ArrayBuffer
  hebrewBold: Uint8Array | ArrayBuffer
  latinBold: Uint8Array | ArrayBuffer
}

export interface TextOp {
  page: number          // 1-based
  x: number
  y: number             // baseline
  width: number
  size: number
  bold: boolean
  text: string          // what the line says (logical order, no bidi controls)
  visual: string        // as drawn, left to right
}

export interface RenderedReport {
  bytes: Uint8Array
  ops: TextOp[]
  pages: number
}

export const REPORT_TITLE = 'דוח השוואת חלופות רכב'
const PAGE: [number, number] = [595.28, 841.89]      // A4 in points
const SIDE = 40
const TOP = 36
const BOTTOM = 58                                    // the footer lives below this line
const MAX_TABLE_OPTIONS = 3                          // per table; more options continue in the next table

type Colour = ReportColour

interface LineOptions {
  size: number
  bold?: boolean
  colour?: Colour
  align?: 'right' | 'left' | 'center'
  dir?: 'rtl' | 'ltr'
}

const money = (v: string | number, signed = false) => ltr(formatMoney(v, { signed }))
/** Amounts inside a sentence ("4,100 ₪ מתוך 4,500 ₪") stay left to right, as on screen. */
const isolateAmounts = (text: string) => text.replace(/[−+-]?\d[\d,.]*\s?₪/g, (m) => ltr(m))

class Writer {
  ops: TextOp[] = []
  pages: PDFPage[] = []
  page!: PDFPage
  y = 0
  readonly doc: PDFDocument
  readonly theme: ReportTheme
  private readonly heb: PDFFont
  private readonly lat: PDFFont
  private readonly hebB: PDFFont
  private readonly latB: PDFFont
  private readonly charsets: Map<PDFFont, Set<number>>
  private onNewPage: () => void = () => undefined

  constructor(doc: PDFDocument, fonts: [PDFFont, PDFFont, PDFFont, PDFFont], theme: ReportTheme) {
    this.doc = doc
    this.theme = theme
    ;[this.heb, this.lat, this.hebB, this.latB] = fonts
    this.charsets = new Map(fonts.map((f) => [f, new Set(f.getCharacterSet())]))
  }

  get left() { return SIDE }
  get right() { return PAGE[0] - SIDE }
  get width() { return PAGE[0] - 2 * SIDE }

  setContinuation(fn: () => void) { this.onNewPage = fn }

  newPage() {
    this.page = this.doc.addPage(PAGE)
    this.pages.push(this.page)
    this.y = PAGE[1] - TOP
  }

  /** Room for `h` more points on this page, else a new page (with the section's continuation header). */
  ensure(h: number) {
    if (this.y - h >= BOTTOM) return
    this.newPage()
    this.onNewPage()
  }

  colour(c: Colour) {
    const [r, g, b] = this.theme[c] as RGB
    return rgb(r / 255, g / 255, b / 255)
  }

  private font(hebrew: boolean, bold: boolean) {
    return hebrew ? (bold ? this.hebB : this.heb) : (bold ? this.latB : this.lat)
  }

  /** A character neither half of the font has (an emoji in a server text, say) becomes a space, never an error. */
  private safe(text: string, bold: boolean) {
    return Array.from(text, (c) => {
      if (/[⁦-⁩]/.test(c)) return c
      const cp = c.codePointAt(0)!
      const hebrew = /[֐-׿יִ-ﭏ₪]/.test(c)
      return this.charsets.get(this.font(hebrew, bold))!.has(cp) ? c : ' '
    }).join('')
  }

  measure(text: string, size: number, bold = false) {
    return fontRuns(plain(this.safe(text, bold))).reduce((w, r) => w + this.font(r.hebrew, bold).widthOfTextAtSize(r.text, size), 0)
  }

  /** One line at (x, y): x is the right edge for right-aligned text, the left edge for left-aligned. */
  line(text: string, x: number, y: number, o: LineOptions) {
    const bold = o.bold ?? false
    const clean = this.safe(text, bold)
    const visual = visualOrder(clean, o.dir ?? 'rtl')
    const runs = fontRuns(visual)
    const width = runs.reduce((w, r) => w + this.font(r.hebrew, bold).widthOfTextAtSize(r.text, o.size), 0)
    const align = o.align ?? 'right'
    let cx = align === 'right' ? x - width : align === 'center' ? x - width / 2 : x
    const start = cx
    for (const r of runs) {
      const font = this.font(r.hebrew, bold)
      this.page.drawText(glyphOrder(r), { x: cx, y, size: o.size, font, color: this.colour(o.colour ?? 'ink') })
      cx += font.widthOfTextAtSize(r.text, o.size)
    }
    this.ops.push({ page: this.pages.indexOf(this.page) + 1, x: start, y, width, size: o.size, bold, text: plain(clean), visual })
    return width
  }

  /** Greedy word wrap in reading order; an isolated amount is never split. */
  wrap(text: string, maxWidth: number, size: number, bold = false): string[] {
    const words: string[] = []
    let depth = 0
    let cur = ''
    for (const c of text) {
      if (c === '⁦') depth++
      if (c === '⁩') depth = Math.max(0, depth - 1)
      if (c === ' ' && depth === 0) { if (cur) words.push(cur); cur = '' } else cur += c
    }
    if (cur) words.push(cur)
    const lines: string[] = []
    let lineText = ''
    for (const w of words) {
      const next = lineText ? `${lineText} ${w}` : w
      if (lineText && this.measure(next, size, bold) > maxWidth) { lines.push(lineText); lineText = w } else lineText = next
    }
    if (lineText) lines.push(lineText)
    return lines.length ? lines : ['']
  }

  /** A rounded rectangle; (x, top) is its top-left corner. */
  box(x: number, top: number, w: number, h: number, fill: Colour, o: { radius?: number; border?: Colour } = {}) {
    const r = Math.min(o.radius ?? 10, h / 2, w / 2)
    const path = `M ${r} 0 H ${w - r} A ${r} ${r} 0 0 1 ${w} ${r} V ${h - r} A ${r} ${r} 0 0 1 ${w - r} ${h} `
      + `H ${r} A ${r} ${r} 0 0 1 0 ${h - r} V ${r} A ${r} ${r} 0 0 1 ${r} 0 Z`
    this.page.drawSvgPath(path, {
      x, y: top, color: this.colour(fill),
      ...(o.border ? { borderColor: this.colour(o.border), borderWidth: 0.8 } : {}),
    })
  }

  /** The Strauss burst as one rule of seven colours. */
  burst(x: number, y: number, w: number, h: number) {
    const cs: Colour[] = ['red', 'orange', 'leaf', 'green', 'sky', 'turq', 'blue']
    const step = w / cs.length
    cs.forEach((c, i) => this.page.drawRectangle({ x: x + i * step, y, width: step + 0.2, height: h, color: this.colour(c) }))
  }

  /** The STRAUSS DRIVE mark: the same petals as the screen's BrandMark, around (cx, cy). */
  mark(cx: number, cy: number, scale: number) {
    for (const [angle, len, c, lean] of PETALS) {
      const name = (/--petal-(\w+)/.exec(c)?.[1] ?? 'blue') as Colour
      this.page.drawSvgPath(petal(len, lean), { x: cx, y: cy, scale, rotate: degrees(-angle), color: this.colour(name) })
    }
  }
}

// ------------------------------------------------------------------ sections

function header(w: Writer, model: ReportModel) {
  const band = 128
  const top = PAGE[1]
  w.page.drawRectangle({ x: 0, y: top - band, width: PAGE[0], height: band, color: w.colour('hero') })
  w.page.drawRectangle({ x: 0, y: top - band, width: PAGE[0] * 0.42, height: band, color: w.colour('heroDeep'), opacity: 0.5 })
  w.burst(0, top - band - 4, PAGE[0], 4)
  w.mark(w.right - 14, top - 40, 0.82)
  // the wordmark reads left to right: STRAUSS (bold) then DRIVE, ending next to the mark
  const wordEnd = w.right - 36
  const driveW = w.measure('DRIVE', 11)
  w.line('DRIVE', wordEnd, top - 46, { size: 11, colour: 'onHero3', dir: 'ltr' })
  w.line('STRAUSS', wordEnd - driveW - 4, top - 46, { size: 11, bold: true, colour: 'onHero', dir: 'ltr' })
  w.line(REPORT_TITLE, w.right, top - 84, { size: 24, bold: true, colour: 'onHero' })
  w.line(`הופק ב-${ltr(generatedText(model.generatedAt))}`, w.right, top - 106, { size: 10.5, colour: 'onHero2' })
  w.y = top - band - 30
}

function continuationHeader(w: Writer, title: string) {
  const top = PAGE[1]
  w.page.drawRectangle({ x: 0, y: top - 30, width: PAGE[0], height: 30, color: w.colour('hero') })
  w.burst(0, top - 33, PAGE[0], 3)
  w.line(`${REPORT_TITLE} · ${title}`, w.right, top - 20, { size: 10, bold: true, colour: 'onHero' })
  w.line('STRAUSS DRIVE', w.left, top - 20, { size: 9.5, bold: true, colour: 'onHero2', align: 'left', dir: 'ltr' })
  w.y = top - 56
}

function sectionTitle(w: Writer, title: string, hint?: string) {
  w.ensure(hint ? 52 : 38)
  w.line(title, w.right, w.y - 14, { size: 14, bold: true })
  w.burst(w.right - 34, w.y - 22, 34, 3)
  w.y -= 30
  if (hint) {
    for (const l of w.wrap(hint, w.width, 9.5)) { w.line(l, w.right, w.y - 10, { size: 9.5, colour: 'ink3' }); w.y -= 13 }
    w.y -= 4
  }
}

function todayCard(w: Writer, model: ReportModel) {
  const t = model.today
  if (!t) return
  const h = 78
  w.ensure(h + 12)
  const top = w.y
  w.box(w.left, top, w.width, h, 'surfaceToday', { radius: 12 })
  const r = w.right - 16
  w.line('היום', r, top - 20, { size: 10, bold: true, colour: 'pending' })
  w.line(t.label, r, top - 40, { size: 15, bold: true })
  w.line(`השתתפות ${money(t.copay)}, שווי שימוש ${money(t.useValue)}`, r, top - 60, { size: 10, colour: 'ink2' })
  const l = w.left + 16
  w.line('הנטו החודשי שלכם היום', l, top - 24, { size: 9.5, colour: 'ink3', align: 'left' })
  w.line(formatMoney(t.net), l, top - 52, { size: 22, bold: true, align: 'left', dir: 'ltr' })
  w.y = top - h - 14
}

function optionCard(w: Writer, o: ReportOption) {
  const onBlue = o.highest
  const text: Colour = onBlue ? 'onHero' : 'ink'
  const soft: Colour = onBlue ? 'onHero2' : 'ink2'
  const quiet: Colour = onBlue ? 'onHero3' : 'ink3'
  const fill: Colour = onBlue ? 'hero' : !o.calculated ? 'card' : o.tone === 'up' ? 'surfaceUp' : o.tone === 'down' ? 'surfaceDown' : 'card'
  const inner = w.width - 32
  const leftColumn = 175                                 // the answer's column; the name wraps before it
  const rankText = `${o.rank}.`
  const rankW = w.measure(`${rankText} `, 14, true)
  const latinName = !/[֐-׿]/.test(o.label)
  const nameLines = w.wrap(o.label, inner - leftColumn - 12 - rankW, 14, true)
  const extra = (nameLines.length - 1) * 17
  const base = (o.calculated ? 92 : 58) + extra
  const notes: string[] = []
  if (o.calculated && o.gapToHighest != null && o.gapToHighest > 0) {
    notes.push(`נמוך ב-${money(o.gapToHighest)} בחודש מהנטו הגבוה ביותר, כלומר ${money(o.gapToHighest * 12)} בשנה.`)
  }
  const why = o.calculated ? o.why : o.reason
  const whyLines = why ? w.wrap(isolateAmounts(why), inner, 9.5) : []
  const noteLines = notes.flatMap((n) => w.wrap(n, inner, 9.5))
  const h = base + (noteLines.length + whyLines.length) * 13 + (whyLines.length || noteLines.length ? 8 : 0)
  w.ensure(h + 10)
  const top = w.y
  w.box(w.left, top, w.width, h, fill, { radius: 12, border: onBlue || o.calculated ? undefined : 'line' })
  if (onBlue) w.burst(w.left + 12, top - 3, w.width - 24, 3)

  // the start side (right): rank, name (a Latin name reads left to right and wraps in its column), status, copay
  const r = w.right - 16
  w.line(rankText, r, top - 24, { size: 14, bold: true, colour: text })
  nameLines.forEach((l, k) => w.line(l, r - rankW, top - 24 - k * 17, { size: 14, bold: true, colour: text, dir: latinName ? 'ltr' : 'rtl' }))
  const rt = top - extra                                // the right column's rows below a wrapped name
  let chipRight = r
  const chip = (label: string, bg: Colour, fg: Colour) => {
    const cw = w.measure(label, 9, true) + 16
    w.box(chipRight - cw, rt - 34, cw, 17, bg, { radius: 8.5 })
    w.line(label, chipRight - 8, rt - 46, { size: 9, bold: true, colour: fg })
    chipRight -= cw + 6
  }
  if (o.highest) chip('הנטו החודשי הגבוה ביותר', 'orange', 'ink')
  chip(o.stateLabel, o.state === 'pending' ? 'pendingTint' : o.state === 'complete' ? 'surfaceUp' : 'card2',
       o.state === 'pending' ? 'pending' : o.state === 'complete' ? 'up' : 'ink2')
  if (o.calculated) {
    const copay = `השתתפות ${money(o.copay!)}${o.copayPending ? ', לפי הקטלוג, ממתינה לאישור נציג' : ''}`
    w.line(copay, r, rt - 66, { size: 9.5, colour: soft })
    w.line(`שווי שימוש ${money(o.useValue!)}`, r, rt - 80, { size: 9.5, colour: soft })
  }

  // the other side (left): the answer
  const l = w.left + 16
  if (o.calculated) {
    w.line('הנטו החדש בחודש', l, top - 22, { size: 9, colour: quiet, align: 'left' })
    w.line(formatMoney(o.net!), l, top - 48, { size: 21, bold: true, colour: text, align: 'left', dir: 'ltr' })
    const changeColour: Colour = onBlue ? (o.tone === 'down' ? 'onHeroDown' : 'onHeroLive') : o.tone === 'down' ? 'down' : o.tone === 'up' ? 'up' : 'ink2'
    w.line(`${money(o.delta!, true)} לעומת היום`, l, top - 66, { size: 10.5, bold: true, colour: changeColour, align: 'left' })
    w.line(`בשנה ${money(o.annual!, true)}`, l, top - 80, { size: 9.5, colour: quiet, align: 'left' })
  } else {
    w.line('אין חישוב לחלופה הזו', l, top - 24, { size: 10.5, bold: true, colour: 'ink2', align: 'left' })
  }

  let y = top - base
  if (noteLines.length || whyLines.length) {
    w.page.drawLine({ start: { x: w.left + 16, y: y + 2 }, end: { x: w.right - 16, y: y + 2 }, thickness: 0.6, color: w.colour(onBlue ? 'heroDeep' : 'line') })
    y -= 8
  }
  for (const n of noteLines) { w.line(n, r, y - 2, { size: 9.5, colour: soft }); y -= 13 }
  for (const n of whyLines) { w.line(n, r, y - 2, { size: 9.5, colour: soft }); y -= 13 }
  w.y = top - h - 10
}

function cellText(cell: Cell): string {
  if (cell.kind === 'money') return money(cell.value, cell.signed)
  if (cell.kind === 'text') return isolateAmounts(cell.text)
  return '—'
}

function breakdownTable(w: Writer, model: ReportModel, from: number, part: number, parts: number) {
  const options = model.options.slice(from, from + MAX_TABLE_OPTIONS)
  const hasToday = model.today != null
  const labelW = 118
  const todayW = hasToday ? 88 : 0
  const optW = (w.width - labelW - todayW) / options.length
  const cols = [
    { x: w.right, w: labelW },
    ...(hasToday ? [{ x: w.right - labelW, w: todayW }] : []),
    ...options.map((_, i) => ({ x: w.right - labelW - todayW - i * optW, w: optW })),
  ]
  const rows = model.rows.filter((r) => r.key !== 'car')     // the head names the cars
  const size = 9.5
  const lineH = 12

  const head = () => {
    const titles = ['פריט', ...(hasToday ? ['היום'] : []), ...options.map((o) => o.label)]
    const wrapped = titles.map((t, i) => w.wrap(t, cols[i].w - 12, size, true))
    const h = Math.max(...wrapped.map((l) => l.length)) * lineH + 12
    const pages = w.pages.length
    w.ensure(h + 30)
    if (w.pages.length !== pages) return          // a new page: its continuation already drew the head
    const top = w.y
    w.box(w.left, top, w.width, h, 'hero', { radius: 8 })
    wrapped.forEach((ls, i) => ls.forEach((l, k) => w.line(l, cols[i].x - 6, top - 14 - k * lineH, {
      size, bold: true, colour: 'onHero', dir: /[֐-׿]/.test(titles[i]) ? 'rtl' : 'ltr',
    })))
    w.y = top - h
  }

  w.ensure(150)                                   // the title, the head and a few rows stay together
  sectionTitle(w, parts > 1 ? `פירוט החישוב (${part} מתוך ${parts})` : 'פירוט החישוב',
               part === 1 ? 'כל הסכומים לחודש, כפי שחושבו במערכת.' : undefined)
  w.setContinuation(() => { continuationHeader(w, 'פירוט החישוב (המשך)'); head() })
  head()

  const optionIndex = (i: number) => from + i
  rows.forEach((row: Row, n) => {
    const cells: string[] = [row.label, ...(hasToday ? [cellText(row.today)] : []),
      ...options.map((_, i) => {
        const c = row.alts[optionIndex(i)]
        const t = cellText(c)
        return row.key === 'copay' && model.options[optionIndex(i)].copayPending && c.kind === 'money' ? `${t} (ממתינה לאישור)` : t
      })]
    const strong = row.key === 'net' || row.key === 'delta'
    const wrapped = cells.map((t, i) => w.wrap(t, cols[i].w - 12, size, strong || i === 0))
    const h = Math.max(...wrapped.map((l) => l.length)) * lineH + 10
    w.ensure(h)
    const top = w.y
    const fill: Colour = row.key === 'net' ? 'hero' : n % 2 === 0 ? 'card' : 'paper'
    w.page.drawRectangle({ x: w.left, y: top - h, width: w.width, height: h, color: w.colour(fill) })
    if (row.key !== 'net') w.page.drawRectangle({ x: w.right - labelW, y: top - h, width: labelW, height: h, color: w.colour('skyTint'), opacity: 0.9 })
    wrapped.forEach((ls, i) => {
      const cell = i === 0 ? null : i === 1 && hasToday ? row.today : row.alts[optionIndex(i - 1 - (hasToday ? 1 : 0))]
      const signed = cell?.kind === 'money' && cell.signed ? Math.sign(Math.round(Number(cell.value))) : 0
      const colour: Colour = row.key === 'net' ? 'onHero' : signed > 0 ? 'up' : signed < 0 ? 'down' : i === 0 ? 'ink' : 'ink2'
      ls.forEach((l, k) => w.line(l, cols[i].x - 6, top - 13 - k * lineH, { size, bold: strong || i === 0, colour }))
    })
    w.page.drawLine({ start: { x: w.left, y: top - h }, end: { x: w.right, y: top - h }, thickness: 0.5, color: w.colour('line') })
    w.y = top - h
  })
  w.setContinuation(() => continuationHeader(w, 'המשך'))
  w.y -= 18
}

function paragraph(w: Writer, text: string, o: { size?: number; colour?: Colour; bullet?: boolean } = {}) {
  const size = o.size ?? 10
  const indent = o.bullet ? 12 : 0
  const lines = w.wrap(isolateAmounts(text), w.width - indent, size)
  for (const [i, l] of lines.entries()) {
    w.ensure(size + 5)
    if (o.bullet && i === 0) w.page.drawCircle({ x: w.right - 3, y: w.y - size + 3.2, size: 1.8, color: w.colour('brand') })
    w.line(l, w.right - indent, w.y - size, { size, colour: o.colour ?? 'ink2' })
    w.y -= size + 4
  }
  w.y -= 4
}

function footers(w: Writer, model: ReportModel) {
  const total = w.pages.length
  w.pages.forEach((page, i) => {
    w.page = page
    const y = 32
    page.drawLine({ start: { x: w.left, y: y + 14 }, end: { x: w.right, y: y + 14 }, thickness: 0.5, color: w.colour('line') })
    w.line(`STRAUSS DRIVE · ${REPORT_TITLE} · הופק ב-${ltr(generatedText(model.generatedAt))}`, w.right, y, { size: 8.5, colour: 'ink3' })
    w.line(`עמוד ${ltr(String(i + 1))} מתוך ${ltr(String(total))}`, w.left, y, { size: 8.5, colour: 'ink3', align: 'left' })
  })
}

// ------------------------------------------------------------------ the report

export async function renderReportPdf(model: ReportModel, fonts: ReportFonts, theme: ReportTheme = DEFAULT_REPORT_THEME): Promise<RenderedReport> {
  const doc = await PDFDocument.create()
  doc.registerFontkit(fontkit)
  const [heb, lat, hebB, latB] = await Promise.all(
    [fonts.hebrew, fonts.latin, fonts.hebrewBold, fonts.latinBold].map((b) => doc.embedFont(b, { subset: true })))
  const w = new Writer(doc, [heb, lat, hebB, latB], theme)
  w.newPage()
  w.setContinuation(() => continuationHeader(w, 'המשך'))
  header(w, model)

  todayCard(w, model)
  sectionTitle(w, 'החלופות שחושבו', 'מסודרות מהשינוי החודשי הגדול ביותר. הסכומים הם הנטו החודשי המשוער, כפי שחושב במערכת.')
  for (const o of model.options) optionCard(w, o)
  w.y -= 6

  const parts = Math.max(1, Math.ceil(model.options.length / MAX_TABLE_OPTIONS))
  for (let p = 0; p < parts; p++) breakdownTable(w, model, p * MAX_TABLE_OPTIONS, p + 1, parts)

  sectionTitle(w, 'כדאיות כספית והעדפה אישית')
  paragraph(w, 'הדירוג בדוח מבוסס על הנטו החודשי המשוער בלבד. בחירת רכב כוללת גם שיקולים אישיים, כמו גודל, נוחות, טווח נסיעה וצורכי המשפחה, ולכן הנטו הגבוה ביותר אינו בהכרח הבחירה הנכונה עבורכם.')

  sectionTitle(w, 'הבהרות')
  paragraph(w, 'הסכומים בדוח הם אומדנים לפי הנתונים שמסרתם, כללי החברה וטבלאות המס, ואינם התחייבות לנטו בפועל. הנטו בתלוש עשוי להיות שונה.', { bullet: true, size: 9.5 })
  if (model.pending) paragraph(w, 'השתתפות שמסומנת "ממתינה לאישור נציג" היא לפי הקטלוג, עד שנציג התחבורה יאשר אותה. אחרי האישור החישוב עשוי להשתנות.', { bullet: true, size: 9.5 })
  paragraph(w, 'הסכום השנתי הוא הסכום החודשי המעוגל כפול 12. הפערים בין החלופות מחושבים מהסכומים המעוגלים שבדוח.', { bullet: true, size: 9.5 })
  for (const a of model.assumptions) paragraph(w, a, { bullet: true, size: 9.5 })
  paragraph(w, `הדוח הופק ב-${ltr(generatedText(model.generatedAt))}, מהתוצאות שהופיעו במסך באותו רגע.`, { bullet: true, size: 9.5 })

  footers(w, model)

  const labels = model.options.map((o) => o.label)
  doc.setTitle(REPORT_TITLE)
  doc.setSubject(`השוואת חלופות רכב: ${labels.join(', ')}`)
  doc.setKeywords([
    ...(model.today ? [`היום ${model.today.label}: ${formatMoney(model.today.net)}`] : []),
    ...model.options.map((o) => `${o.label}: ${o.calculated ? formatMoney(o.net!) : 'אין חישוב'}`),
  ])
  doc.setAuthor('STRAUSS DRIVE')
  doc.setCreator('STRAUSS DRIVE')
  doc.setProducer('STRAUSS DRIVE')
  doc.setLanguage('he-IL')
  doc.setCreationDate(model.generatedAt)
  doc.setModificationDate(model.generatedAt)
  doc.catalog.getOrCreateViewerPreferences().setReadingDirection(ReadingDirection.R2L)

  const bytes = await doc.save()
  return { bytes, ops: w.ops, pages: w.pages.length }
}
