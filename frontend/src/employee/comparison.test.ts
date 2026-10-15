import { describe, expect, it } from 'vitest'
import { buildRows, optionIds } from './comparison'
import type { OptionResult } from '../api/types'

const bd = (net: string, copay = '0', fam = '0') => ({ label: 'x', cash_gross: '0', use_value: '2500', maintenance: '0',
  electric_gross_up: '0', pension: '0', study_fund: '0', income_tax: '0', national_insurance: '0', health: '0',
  copay, family_share: fam, net, net_display: net })
const item = (id: string, copay = '350', fam = '0'): OptionResult => ({
  option_id: id, label: id, run_id: 'r',
  result: { status: 'OK', eligible: true, reason: '', missing_fields: [], today: bd('10000'), alternative: bd('10611', copay, fam),
    delta_display: '611', lines: [], cost: null, drivers: [], electric_refund: null, copay_pending: false, assumptions: [],
    rules_version: '', extensions_version: '', engine_version: '' },
})

describe('buildRows', () => {
  it('has one alternative cell per option and shows the confirmed copay', () => {
    const rows = buildRows([item('ATTO 3'), item('MG4', '0')])
    const copay = rows.find((r) => r.key === 'copay')!
    expect(copay.alts).toEqual([{ kind: 'money', value: '350', signed: false }, { kind: 'money', value: '0', signed: false }])
    expect(rows.find((r) => r.key === 'delta')!.alts[0]).toEqual({ kind: 'money', value: '611', signed: true })
  })
  it('adds the family row only when a share exists', () => {
    expect(buildRows([item('a')]).some((r) => r.key === 'family')).toBe(false)
    expect(buildRows([item('a', '0', '300')]).some((r) => r.key === 'family')).toBe(true)
  })
})

describe('optionIds', () => {
  const f = { field: 'options', choices: [{ id: 'A' }, { id: 'OPT_OUT' }] }
  it('reads the ids from the options field of expects', () => {
    expect(optionIds({ ...f, fields: [f] })).toEqual(['A', 'OPT_OUT'])
  })
  it('is empty without options', () => {
    expect(optionIds({ field: null, choices: [], fields: [] })).toEqual([])
    expect(optionIds(null)).toEqual([])
  })
})
