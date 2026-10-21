// How a result is presented, from ScenarioResult only. Nothing here adds or subtracts money: every amount shown is a
// server *_display value; the only arithmetic is the relative size of a bar or a shape (|value| / largest |value|).
import type { Driver, OptionResult, ScenarioResult } from '../api/types'
import { buildRows } from './comparison'
import type { Row } from './comparison'
import { driverLabel } from './labels'

export type ResultState = 'pending' | 'complete' | 'incomplete' | 'not_eligible'

export function resultState(r: ScenarioResult): ResultState {
  if (r.status === 'NOT_ELIGIBLE') return 'not_eligible'
  if (r.status !== 'OK' || r.delta_display == null) return 'incomplete'
  return r.copay_pending ? 'pending' : 'complete'
}

/** Short status names: a temporary estimate is never presented as final. */
export const STATE_LABEL: Record<ResultState, string> = {
  pending: 'הערכה זמנית',
  complete: 'חישוב מלא',
  incomplete: 'חסרים פרטים לחישוב',
  not_eligible: 'לא בזכאות',
}

export const STATE_NOTE: Record<ResultState, string> = {
  pending: 'ההשתתפות לפי הקטלוג, ממתינה לאישור נציג',
  complete: 'לפי הנתונים שמסרתם',
  incomplete: 'אין חישוב בלי כל הנתונים',
  not_eligible: 'הרכב לא זמין לכם',
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
