// How a result is presented, from ScenarioResult only. Nothing here adds or subtracts money: every amount shown is a
// server *_display value; the only arithmetic is the relative size of a bar or a shape (|value| / largest |value|).
import type { Driver, OptionResult, ScenarioResult } from '../api/types'
import { buildRows } from './comparison'
import type { Row } from './comparison'
import { driverLabel } from './labels'

export type ResultState = 'pending' | 'approved' | 'complete' | 'incomplete' | 'not_eligible'

export function resultState(r: ScenarioResult): ResultState {
  if (r.status === 'NOT_ELIGIBLE') return 'not_eligible'
  if (r.status !== 'OK' || r.delta_display == null) return 'incomplete'
  if (r.copay_pending) return 'pending'
  return r.copay_source === 'representative' ? 'approved' : 'complete'
}

/** Short status names: a temporary estimate is never presented as final, and "approved" only with a saved
 *  answer of the representative (copay_source from the server, never inferred here). */
export const STATE_LABEL: Record<ResultState, string> = {
  pending: 'הערכה זמנית',
  approved: 'אושר על ידי הנציג',
  complete: 'חישוב מלא',
  incomplete: 'חסרים פרטים לחישוב',
  not_eligible: 'לא בזכאות',
}

export const PENDING_COPAY_LABEL = 'השתתפות לפי ברירת המחדל בקטלוג — ממתינה לאישור הנציג'

export const STATE_NOTE: Record<ResultState, string> = {
  pending: PENDING_COPAY_LABEL,
  approved: 'ההשתתפות אושרה על ידי נציג התחבורה',
  complete: 'לפי הנתונים שמסרתם',
  incomplete: 'אין חישוב בלי כל הנתונים',
  not_eligible: 'הרכב לא זמין לכם',
}

const dayNumber = (y: number, m: number, d: number) => y * 10000 + m * 100 + d

/** The representative's date: "תחול מ-01.01.2027" while it is still ahead of today (never shown as in force
 *  before it is), "בתוקף מ-…" from that day on. `today` is the viewer's local date. */
export function effectiveText(iso: string | null | undefined, today: Date = new Date()): string | null {
  const m = iso ? /^(\d{4})-(\d{2})-(\d{2})/.exec(iso) : null
  if (!m) return null
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])]
  const shown = `${m[3]}.${m[2]}.${m[1]}`
  const future = dayNumber(y, mo, d) > dayNumber(today.getFullYear(), today.getMonth() + 1, today.getDate())
  return future ? `תחול מ-${shown}` : `בתוקף מ-${shown}`
}

export interface CopayInfo {
  amount: string | null       // the amount the calculation used (server value); null when there is none
  source: string              // where it comes from
  status: string              // approved or waiting
  tone: 'pending' | 'approved' | 'final' | 'missing'
  effective: string | null    // "תחול מ-…" / "בתוקף מ-…"
  effect: string | null       // the copay's effect on the net against today (the engine's COPAY driver)
  stated: string | null       // what the employee said they expect (shown, never used)
}

/** The new car's copay, per alternative: amount, source, approval status, effect on the net and the date it
 *  applies from. Everything comes from the server's fields; null for an option with no copay (opting out). */
export function copayInfo(r: ScenarioResult, today: Date = new Date()): CopayInfo | null {
  const effect = r.drivers.find((d) => d.code === 'COPAY')?.net_effect_display ?? null
  const amount = r.alternative?.copay ?? null
  const stated = r.employee_stated_copay ?? null
  switch (r.copay_source) {
    case 'catalog':
      return { amount, source: 'לפי ברירת המחדל בקטלוג', status: 'ממתינה לאישור הנציג', tone: 'pending', effective: null, effect, stated }
    case 'representative':
      return { amount, source: 'לפי נציג התחבורה', status: 'אושר על ידי הנציג', tone: 'approved',
               effective: effectiveText(r.copay_effective_date, today), effect, stated: null }
    case 'catalog_final':
      return { amount, source: 'לפי הקטלוג', status: 'סופית', tone: 'final', effective: null, effect, stated }
    case 'missing':
      return { amount: null, source: 'אין לרכב הזה סכום בקטלוג', status: 'שאלתי את נציג התחבורה', tone: 'missing',
               effective: null, effect: null, stated }
    default:
      return null
  }
}

export type Tone = 'up' | 'down' | 'flat'
export const toneOf = (value: string | number | null | undefined): Tone => {
  const n = Math.round(Number(value))
  return !Number.isFinite(n) || n === 0 ? 'flat' : n > 0 ? 'up' : 'down'
}

export interface DriverBar {
  code: string
  label: string
  value: string          // net_effect_display, as the server sent it
  tone: Tone
  share: number          // 0..1, for the bar length only
}

/** What moved the net, largest first, with each bar's length relative to the largest effect. */
export function driverBars(drivers: Driver[]): DriverBar[] {
  const sorted = [...drivers].sort((a, b) => Math.abs(Number(b.net_effect_display)) - Math.abs(Number(a.net_effect_display)))
  const max = Math.max(1, ...sorted.map((d) => Math.abs(Number(d.net_effect_display))))
  return sorted.map((d) => ({
    code: d.code, label: driverLabel(d.code), value: d.net_effect_display, tone: toneOf(d.net_effect_display),
    share: Math.abs(Number(d.net_effect_display)) / max,
  }))
}

/** Relative size of each option's monthly change (for the abstract shape next to it). */
export function deltaShares(items: OptionResult[]): Record<string, number> {
  const ok = items.filter((i) => i.result.status === 'OK' && i.result.delta_display != null)
  const max = Math.max(1, ...ok.map((i) => Math.abs(Number(i.result.delta_display))))
  return Object.fromEntries(ok.map((i) => [i.option_id, Math.abs(Number(i.result.delta_display)) / max]))
}

/** The breakdown table: the comparison rows plus the tax lines the engine reports (income tax, NI and health),
 *  inserted before the net. Values are the lines' before_display / after_display. */
export function breakdownRows(items: OptionResult[]): Row[] {
  const rows = buildRows(items)
  const ok = (i: OptionResult) => i.result.status === 'OK' && i.result.alternative != null
  const line = (i: OptionResult, code: string) => i.result.lines.find((l) => l.code === code)
  const extra: Row[] = []
  for (const [code, fallback] of [['INCOME_TAX', 'מס הכנסה'], ['SOCIAL', 'ביטוח לאומי ובריאות']] as const) {
    const first = items.find((i) => ok(i) && line(i, code))
    if (!first) continue
    const today = line(first, code)!.before_display
    extra.push({
      key: code.toLowerCase(), label: line(first, code)!.label || fallback,
      today: today == null ? { kind: 'none' } : { kind: 'money', value: today, signed: false },
      alts: items.map((i) => {
        const l = ok(i) ? line(i, code) : undefined
        return l?.after_display == null ? { kind: 'none' } : { kind: 'money', value: l.after_display, signed: false }
      }),
    })
  }
  const at = rows.findIndex((r) => r.key === 'net')
  return at < 0 ? [...rows, ...extra] : [...rows.slice(0, at), ...extra, ...rows.slice(at)]
}
