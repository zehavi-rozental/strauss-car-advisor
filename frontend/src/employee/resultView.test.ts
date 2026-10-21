import { describe, expect, it } from 'vitest'
import type { OptionResult, ScenarioResult } from '../api/types'
import { breakdownRows, deltaShares, driverBars, resultState, toneOf } from './resultView'

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
