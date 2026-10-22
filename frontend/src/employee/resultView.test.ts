import { describe, expect, it } from 'vitest'
import type { OptionResult, ScenarioResult } from '../api/types'
import { PENDING_COPAY_LABEL, STATE_LABEL, STATE_NOTE, breakdownRows, copayInfo, deltaShares, driverBars,
  effectiveText, resultState, toneOf } from './resultView'

const bd = (net: string) => ({ label: 'x', cash_gross: '0', use_value: '0', maintenance: '0', electric_gross_up: '0',
  pension: '0', study_fund: '0', income_tax: '0', national_insurance: '0', health: '0', copay: '0', family_share: '0',
  net, net_display: net })
const res = (extra: Partial<ScenarioResult> = {}): ScenarioResult => ({
  status: 'OK', eligible: true, reason: '', missing_fields: [], today: bd('11032'), alternative: bd('11693'),
  delta_display: '661', cost: null, electric_refund: null, copay_pending: false, assumptions: [], rules_version: '',
  extensions_version: '', engine_version: '', drivers: [], ...extra,
  lines: extra.lines ?? [
    { code: 'INCOME_TAX', label: 'מס הכנסה', before: '3443.15', after: '3282.9', change: '-160.25', before_display: '3443',
      after_display: '3283', change_display: '-160', note: null },
    { code: 'SOCIAL', label: 'ביטוח לאומי ובריאות', before: '2152.53', after: '2091.6', change: '-60.9', before_display: '2153',
      after_display: '2092', change_display: '-61', note: null },
  ],
})
const item = (id: string, r: ScenarioResult): OptionResult => ({ option_id: id, label: id, run_id: 'r', result: r })

describe('result state', () => {
  it('never calls a pending copay final', () => {
    expect(resultState(res({ copay_pending: true }))).toBe('pending')
    expect(resultState(res())).toBe('complete')
    expect(resultState(res({ status: 'INCOMPLETE', delta_display: null }))).toBe('incomplete')
    expect(resultState(res({ status: 'NOT_ELIGIBLE', delta_display: null }))).toBe('not_eligible')
  })
  it("says approved only with the representative's saved answer, never for a catalog amount", () => {
    expect(resultState(res({ copay_source: 'representative' }))).toBe('approved')
    expect(STATE_LABEL.approved).toBe('אושר על ידי הנציג')
    expect(resultState(res({ copay_source: 'catalog', copay_pending: true }))).toBe('pending')
    expect(resultState(res({ copay_source: 'catalog_final' }))).toBe('complete')
    expect(STATE_NOTE.pending).toBe('השתתפות לפי ברירת המחדל בקטלוג — ממתינה לאישור הנציג')
    expect(PENDING_COPAY_LABEL).toBe(STATE_NOTE.pending)
  })
  it('reads the tone from the server value', () => {
    expect([toneOf('661'), toneOf('-3522'), toneOf('0'), toneOf(null)]).toEqual(['up', 'down', 'flat', 'flat'])
  })
})

describe('driver bars', () => {
  it('orders by size, keeps the server values, and only scales the bars', () => {
    const bars = driverBars([
      { code: 'COPAY', amount: '-50', amount_display: '-50', net_effect: '50', net_effect_display: '50', tax_change: null, tax_change_display: null, net: true },
      { code: 'USE_VALUE', amount: '-1640', amount_display: '-1640', net_effect: '713.2', net_effect_display: '713', tax_change: null, tax_change_display: null, net: true },
    ])
    expect(bars.map((b) => [b.code, b.value, b.tone])).toEqual([['USE_VALUE', '713', 'up'], ['COPAY', '50', 'up']])
    expect(bars[0].share).toBe(1)
    expect(bars[1].share).toBeCloseTo(50 / 713)
  })
})

describe('shapes and breakdown', () => {
  it('sizes each option by its change relative to the largest', () => {
    expect(deltaShares([item('A', res()), item('B', res({ delta_display: '-1322' }))])).toEqual({ A: 661 / 1322, B: 1 })
  })
  it('adds the engine tax lines before the net, with the server display values', () => {
    const rows = breakdownRows([item('A', res())])
    const keys = rows.map((r) => r.key)
    expect(keys.indexOf('income_tax')).toBeLessThan(keys.indexOf('net'))
    expect(rows.find((r) => r.key === 'income_tax')).toMatchObject({
      today: { kind: 'money', value: '3443' }, alts: [{ kind: 'money', value: '3283' }],
    })
    expect(rows.find((r) => r.key === 'social')!.label).toBe('ביטוח לאומי ובריאות')
  })
  it('shows no tax row for an option that was not calculated', () => {
    const rows = breakdownRows([item('A', res()), item('B', res({ status: 'NOT_ELIGIBLE', delta_display: null, alternative: null }))])
    expect(rows.find((r) => r.key === 'income_tax')!.alts[1]).toEqual({ kind: 'none' })
  })
})

describe('the new car copay, per option (C1)', () => {
  const today = new Date(2026, 9, 22)          // 22.10.2026, the viewer's local date
  const copayDriver = { code: 'COPAY', amount: '-50', amount_display: '-50', net_effect: '-50', net_effect_display: '-50',
    tax_change: null, tax_change_display: null, net: true }
  const withCopay = (copay: string, extra: Partial<ScenarioResult>) =>
    res({ alternative: { ...bd('11643'), copay }, drivers: [copayDriver], ...extra })

  it('a future date is never shown as in force today (D7)', () => {
    expect(effectiveText('2027-01-01', today)).toBe('תחול מ-01.01.2027')
    expect(effectiveText('2026-10-22', today)).toBe('בתוקף מ-22.10.2026')
    expect(effectiveText('2026-01-01', today)).toBe('בתוקף מ-01.01.2026')
    expect(effectiveText(null, today)).toBeNull()
  })
  it('the catalog default: amount, source, waiting, and its effect on the net from the engine', () => {
    expect(copayInfo(withCopay('300', { copay_source: 'catalog', copay_pending: true }), today)).toEqual({
      amount: '300', source: 'לפי ברירת המחדל בקטלוג', status: 'ממתינה לאישור הנציג', tone: 'pending',
      effective: null, effect: '-50', stated: null })
  })
  it('the representative: approved, with the date it applies from', () => {
    const c = copayInfo(withCopay('350', { copay_source: 'representative', copay_effective_date: '2027-01-01' }), today)!
    expect([c.amount, c.status, c.tone, c.effective]).toEqual(['350', 'אושר על ידי הנציג', 'approved', 'תחול מ-01.01.2027'])
  })
  it('an amount the employee expects is shown next to the catalog one, never as the amount used', () => {
    const c = copayInfo(withCopay('150', { copay_source: 'catalog', copay_pending: true, employee_stated_copay: '300' }), today)!
    expect([c.amount, c.stated, c.status]).toEqual(['150', '300', 'ממתינה לאישור הנציג'])
  })
  it('no catalog amount: no amount, no effect, and the representative was asked (C2)', () => {
    const c = copayInfo(res({ status: 'INCOMPLETE', delta_display: null, alternative: null, copay_source: 'missing' }), today)!
    expect([c.amount, c.effect, c.tone]).toEqual([null, null, 'missing'])
  })
  it('no copay line for opting out or an older server', () => {
    expect(copayInfo(res({ copay_source: null }), today)).toBeNull()
    expect(copayInfo(res(), today)).toBeNull()
  })
})
