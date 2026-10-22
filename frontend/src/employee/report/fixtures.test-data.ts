// Synthetic results for the report tests (no real employee): shaped exactly like the server's OptionResult.
import type { Breakdown, OptionResult, ScenarioResult } from '../../api/types'

export function side(label: string, net: number, copay = 300, useValue = 4070): Breakdown {
  return {
    label, cash_gross: '18500', use_value: String(useValue), maintenance: '0', electric_gross_up: '0', pension: '1110',
    study_fund: '462.50', income_tax: '3443', national_insurance: '1500', health: '653', copay: String(copay),
    family_share: '0', net: `${net}.37`, net_display: String(net),
  }
}

export const TODAY = side('COROLLA CROSS (2025)', 11032)

export function result(label: string, net: number | null, extra: Partial<ScenarioResult> = {}): ScenarioResult {
  const ok = net != null
  const waiver = label === 'ויתור על הרכב'                 // no car: no copay and no use value
  return {
    status: ok ? 'OK' : 'NOT_ELIGIBLE', eligible: ok, reason: ok ? 'הרכב מהקבוצה שלכם או נמוכה ממנה' : 'הרכב לא בקבוצת הזכאות שלכם',
    missing_fields: [], today: TODAY, alternative: ok ? side(label, net, waiver ? 0 : 250, waiver ? 0 : 2430) : null,
    delta_display: ok ? String(net - 11032) : null,
    lines: ok ? [
      { code: 'INCOME_TAX', label: 'מס הכנסה', before: '3443', after: '3066', change: '-377', before_display: '3443', after_display: '3066', change_display: '-377', note: null },
      { code: 'SOCIAL', label: 'ביטוח לאומי ובריאות', before: '2153', after: '2006', change: '-147', before_display: '2153', after_display: '2006', change_display: '-147', note: null },
    ] : [],
    cost: ok ? { group: 'ג', monthly_cost: '3550', monthly_cost_display: '3550', budget: '4500', budget_display: '4500', under_budget: true } : null,
    drivers: [], electric_refund: null, copay_pending: ok, assumptions: ['חישוב חודשי לפי כללי החברה ולוחות המס של 2025'],
    why: ok ? 'שווי השימוש נמוך יותר, ולכן המס יקטן.' : undefined,
    rules_version: '', extensions_version: '', engine_version: '', ...extra,
  }
}

export const item = (id: string, label: string, net: number | null, extra: Partial<ScenarioResult> = {}): OptionResult =>
  ({ option_id: id, label, run_id: `r-${id}`, result: result(label, net, extra) })

/** Three cars as the comparison holds them (in the order chosen, not ranked). */
export const THREE: OptionResult[] = [
  item('CAR-10', 'ATTO 3 (2025)', 11693),
  item('CAR-04', 'MG4 (2025)', 12045),
  item('CAR-11', 'EV3 (2025)', 11779),
]

/** More than 3, with a waiver, a loss, a long name and an option without a result. */
export const MANY: OptionResult[] = [
  ...THREE,
  item('CAR-13', 'KONA EV (2026)', 10109),
  item('OPT_OUT', 'ויתור על הרכב', 14594, { copay_pending: false, cost: null, why: 'בלי רכב אין שווי שימוש, ולכן המס יקטן.' }),
  item('CAR-99', 'MERCEDES-BENZ EQE 350+ ELECTRIC ART EXECUTIVE LINE (2026)', null),
]
