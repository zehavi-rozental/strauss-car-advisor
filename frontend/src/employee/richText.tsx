// Bubble text: a safe markdown subset (line breaks, **bold**, "- " bullets). React escapes everything,
// so there is no raw HTML. Every amount is wrapped in <bdi> so signs and ₪ never flip in RTL text.
import type { ReactNode } from 'react'

// an amount: optional sign (not the hyphen of "ב-923"), digits with separators, then ₪
const AMOUNT = /((?<![\p{L}\d])[+−-])?\d[\d,]*(?:\.\d+)?\s?₪/gu
const BOLD = /\*\*([^*\n]+)\*\*/g

/** One sign style everywhere: a minus is always "−" (U+2212). */
export const normalizeSign = (amount: string) => amount.replace(/^-/, '−')

// a Latin model name, with its year in parentheses when there is one ("BYD DOLPHIN (2025)", "MG4", "ID.4"): kept
// whole and left-to-right inside Hebrew text, so the year's parentheses never flip (D2, 29/10)
// (catalog names are upper case; other Latin text, an address or a link, is left as it is). A Hebrew prefix
// joined by a hyphen ("ב-", "וב-") travels with the name: the line never breaks after "ב-" or inside the name
// while the whole fits on a line (the run is an inline block no wider than the line, so a longer one still wraps)
const LATIN = /(?:([א-ת]{1,3})-)?\b([A-Z][A-Z0-9.'-]*(?:[  ]+[A-Z0-9][A-Z0-9.'-]*)*(?:[  ]\(\d{4}\))?)/g

function latin(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(LATIN)) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const name = <bdi dir="ltr" className="latin">{m[2]}</bdi>
    out.push(<span key={`${key}-l${m.index}`} className="latin-run">{m[1] ? `${m[1]}-` : null}{name}</span>)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

function amounts(text: string, key: string): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(AMOUNT)) {
    if (m.index > last) out.push(...latin(text.slice(last, m.index), `${key}-${last}`))
    out.push(<bdi key={`${key}-${m.index}`} className="money amt">{normalizeSign(m[0])}</bdi>)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(...latin(text.slice(last), `${key}-${last}`))
  return out
}

export function inline(text: string, key = 'i'): ReactNode[] {
  const out: ReactNode[] = []
  let last = 0
  for (const m of text.matchAll(BOLD)) {
    if (m.index > last) out.push(...amounts(text.slice(last, m.index), `${key}-${last}`))
    out.push(<strong key={`${key}-b${m.index}`}>{amounts(m[1], `${key}-b${m.index}`)}</strong>)
    last = m.index + m[0].length
  }
  if (last < text.length) out.push(...amounts(text.slice(last), `${key}-${last}`))
  return out
}

const BULLET = /^\s*[-•*]\s+(.*)$/

export function RichText({ text }: { text: string }) {
  const blocks: ReactNode[] = []
  let bullets: string[] = []
  let lines: string[] = []
  const flushLines = () => {
    if (!lines.length) return
    const k = blocks.length
    blocks.push(
      <p key={`p${k}`}>
        {lines.map((l, i) => <span key={i}>{i > 0 && <br />}{inline(l, `p${k}-${i}`)}</span>)}
      </p>,
    )
    lines = []
  }
  const flushBullets = () => {
    if (!bullets.length) return
    const k = blocks.length
    blocks.push(<ul key={`u${k}`}>{bullets.map((b, i) => <li key={i}>{inline(b, `u${k}-${i}`)}</li>)}</ul>)
    bullets = []
  }
  for (const line of text.split('\n')) {
    const b = BULLET.exec(line)
    if (b) {
      flushLines()
      bullets.push(b[1])
    } else if (line.trim() === '') {
      flushLines()
      flushBullets()
    } else {
      flushBullets()
      lines.push(line)
    }
  }
  flushLines()
  flushBullets()
  return <div className="rich">{blocks}</div>
}
