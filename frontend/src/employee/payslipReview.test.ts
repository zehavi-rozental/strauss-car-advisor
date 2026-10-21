import { describe, expect, it } from 'vitest'
import type { ExtractedField, PayslipInfo } from '../api/types'
import { profileFormText } from './formFields'
import { NOT_ON_PAYSLIP } from './payslipOutcome'
import { PAYSLIP_FIELDS, assign, assignable, reviewFields, reviewRows } from './payslipReview'

const f = (field: ExtractedField['field'], value: string | null, extra: Partial<ExtractedField> = {}): ExtractedField => ({
  field, label: field, value, kind: 'amount', confident: true, in_range: true, note: '',
  status: value === null ? 'missing' : 'found', source_label: '', certainty: 'high', ...extra,
})

// the synthetic test payslip: base 18,000 found; the addition and the pension amount are information
const SYNTHETIC: ExtractedField[] = [
  f('base_salary', '18000', { source_label: 'שכר יסוד' }), f('commissions', null), f('bonus', null),
  f('pension_percent', null), f('study_fund_percent', null), f('credit_points', null), f('children_under_23', null),
]
const ADDITION: PayslipInfo = { key: 'other_addition', label: 'תוספת לשכר', value: '1000', kind: 'amount',
  source_label: 'תוספת חודשית', note: '...' }

describe('payslip review', () => {
  it('has a row for every field the conversation accepts, base salary first', () => {
    expect(PAYSLIP_FIELDS.map((s) => s.key)).toEqual(['base_salary', 'commissions', 'bonus', 'pension_percent',
      'study_fund_percent', 'credit_points', 'children_under_23'])
  })

  it('keeps a confident value and leaves the missing ones empty and unchecked', () => {
    const rows = reviewRows(SYNTHETIC)
    const base = rows[0]
    expect(base).toMatchObject({ value: '18000', keep: true, status: 'found', source: 'בתלוש: שכר יסוד' })
    for (const r of rows.slice(1)) expect(r).toMatchObject({ value: '', keep: false, status: 'missing', note: NOT_ON_PAYSLIP })
    expect(reviewFields(rows)).toEqual({ fields: { base_salary: 18000 }, errors: {} })
  })

  it('does not keep a value that contradicts what the employee said, and says so', () => {
    const rows = reviewRows(SYNTHETIC, { base: '15000' })
    expect(rows[0]).toMatchObject({ value: '18000', keep: false, differs: true, known: '15,000 ₪' })
    expect(reviewFields(rows).fields).toEqual({})
  })

  it('does not keep a low-certainty or ambiguous value', () => {
    const rows = reviewRows([f('bonus', '500', { confident: false, note: 'לא ודאי — נא לבדוק' }),
      f('pension_percent', '60', { status: 'ambiguous', confident: false, in_range: false, note: 'מחוץ לטווח' })])
    const bonus = rows.find((r) => r.key === 'bonus')!
    const pension = rows.find((r) => r.key === 'pension_percent')!
    expect(bonus).toMatchObject({ value: '500', keep: false })
    expect(pension).toMatchObject({ value: '', keep: false, status: 'ambiguous' })
    expect(pension.note).toContain('60%')
  })

  it('keeps fractions: credit points 2.75', () => {
    const rows = reviewRows([f('credit_points', '2.75')])
    expect(reviewFields(rows).fields).toEqual({ credit_points: 2.75 })
  })

  it('a checked row without a valid value is an error, never sent', () => {
    const rows = reviewRows([]).map((r) => (r.key === 'children_under_23' ? { ...r, keep: true, value: '2.5' } : r))
    expect(reviewFields(rows)).toEqual({ fields: {}, errors: { children_under_23: 'מספר שלם, 0 או יותר' } })
    const empty = reviewRows([]).map((r) => (r.key === 'bonus' ? { ...r, keep: true } : r))
    expect(reviewFields(empty).errors.bonus).toBeDefined()
  })

  it('an unclassified addition can be assigned by the employee to an empty commissions or bonus row', () => {
    const rows = reviewRows(SYNTHETIC)
    expect(assignable(ADDITION, rows)).toEqual(['commissions', 'bonus'])
    const assigned = assign(rows, 'bonus', ADDITION)
    expect(assigned.find((r) => r.key === 'bonus')).toMatchObject({ value: '1000', keep: true, note: 'שויך על ידיכם' })
    expect(assignable(ADDITION, assigned)).toEqual(['commissions'])     // never added to a filled row
    expect(assignable({ ...ADDITION, key: 'net_pay' }, rows)).toEqual([])
  })

  it('the chat bubble names the base salary', () => {
    expect(profileFormText({ base_salary: 18000, bonus: 0 })).toBe('שכר בסיס 18,000 ₪, אין בונוס')
  })
})
