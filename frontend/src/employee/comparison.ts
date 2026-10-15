// Rows of the comparison table, from ScenarioResult only. Values are the server's *_display strings
// (or raw Decimals for lines that have no display twin); nothing is calculated here.
import type { OptionResult } from '../api/types'

export type Cell =
  | { kind: 'text'; text: string; tag?: string }
  | { kind: 'money'; value: string; signed?: boolean }
  | { kind: 'none' }

export interface Row {
  key: string
  label: string
  today: Cell
  alts: Cell[]
  strong?: boolean
}

const NONE: Cell = { kind: 'none' }
const money = (v: string | null | undefined, signed = false): Cell =>
  v == null ? NONE : { kind: 'money', value: v, signed }
const isZero = (v: string | null | undefined) => v == null || Number(v) === 0

export function buildRows(items: OptionResult[]): Row[] {
  const first = items.find((i) => i.result.today)?.result.today ?? null
  const ok = (i: OptionResult) => i.result.status === 'OK' && i.result.alternative != null
  const alt = (i: OptionResult) => i.result.alternative
  const rows: Row[] = [
    { key: 'car', label: 'רכב', today: first ? { kind: 'text', text: first.label } : NONE,
      alts: items.map((i) => ({ kind: 'text', text: i.label })) },
    { key: 'eligible', label: 'זכאות',
      today: first ? { kind: 'text', text: 'רכב נוכחי' } : NONE,
      alts: items.map((i) => {
        const r = i.result
        const state = r.eligible === false ? 'לא זכאי' : r.eligible === true ? 'זכאי' : 'ממתין לבירור'
        return { kind: 'text', text: r.reason ? `${state}: ${r.reason}` : state }
      }) },
    { key: 'cost', label: 'עלות / תקציב', today: NONE,
      alts: items.map((i) => i.result.cost
        ? { kind: 'text', text: `${fmt(i.result.cost.monthly_cost_display)} מתוך ${fmt(i.result.cost.budget_display)}` }
        : NONE) },
    { key: 'copay', label: 'ההשתתפות שלך', today: ok0(first?.copay),
      alts: items.map((i) => (ok(i) ? money(alt(i)!.copay) : NONE)) },
    { key: 'use_value', label: 'שווי שימוש', today: ok0(first?.use_value),
      alts: items.map((i) => (ok(i) ? money(alt(i)!.use_value) : NONE)) },
    { key: 'electric', label: 'החזר חשמל / אחזקה', today: first ? combo(first.electric_gross_up, first.maintenance) : NONE,
      alts: items.map((i) => (ok(i) ? combo(alt(i)!.electric_gross_up, alt(i)!.maintenance) : NONE)) },
  ]
  if (items.some((i) => ok(i) && !isZero(alt(i)!.family_share))) {
    rows.push({ key: 'family', label: 'חלק במסלול המשפחתי', today: NONE,
                alts: items.map((i) => (ok(i) ? money(alt(i)!.family_share) : NONE)) })
  }
  rows.push(
    { key: 'net', label: 'נטו משוער', strong: true, today: first ? money(first.net_display) : NONE,
      alts: items.map((i) => (ok(i) ? money(alt(i)!.net_display) : NONE)) },
    { key: 'delta', label: 'הפרש מול היום', strong: true, today: NONE,
      alts: items.map((i) => (ok(i) ? money(i.result.delta_display, true) : NONE)) },
  )
  return rows
}

function fmt(v: string): string {
  return `${Math.round(Number(v)).toLocaleString('he-IL')} ₪`
}
function ok0(v: string | undefined): Cell {
  return v === undefined ? NONE : money(v)
}
/** electric refund and maintenance are one row: both shown when both exist */
function combo(electric: string, maintenance: string): Cell {
  const e = !isZero(electric), m = !isZero(maintenance)
  if (e && m) return { kind: 'text', text: `החזר ${fmt(electric)} · אחזקה ${fmt(maintenance)}` }
  if (e) return money(electric)
  if (m) return money(maintenance)
  return money('0')
}

/** ids for the buttons, from expects only (never from /api/catalog) */
export function optionIds(expects: { field: string | null; choices: { id: string }[]; fields: { field: string | null; choices: { id: string }[] }[] } | null): string[] {
  if (!expects) return []
  const f = expects.fields.find((x) => x.field === 'options') ?? (expects.field === 'options' ? expects : null)
  return f ? f.choices.map((c) => c.id) : []
}

export const OPT_OUT_ID = 'OPT_OUT'
