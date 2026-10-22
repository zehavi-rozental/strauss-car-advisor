import { describe, expect, it } from 'vitest'
import type { ExtractedField, PayslipInfo } from '../api/types'
import { profileFormText } from './formFields'
import { NOT_ON_PAYSLIP } from './payslipOutcome'
import { CHOOSE_FIRST, NOT_COMPARED, PAYSLIP_FIELDS, assign, assignable, choicesFor, reviewFields, reviewRows } from './payslipReview'

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
  source_label: 'תוספת חודשית', note: '...', suspect: false, assignable: true }

describe('payslip review', () => {
  it('has a row for every field the conversation accepts, base salary first', () => {
    expect(PAYSLIP_FIELDS.map((s) => s.key)).toEqual(['base_salary', 'commissions', 'bonus', 'pension_percent',
      'study_fund_percent', 'credit_points', 'children_under_23'])
  })

  it('preselects a confident value for an empty field and leaves the missing ones as "not updated"', () => {
    const rows = reviewRows(SYNTHETIC)
    const base = rows[0]
    expect(base).toMatchObject({ value: '18000', choice: 'payslip', status: 'found', source: 'בתלוש: שכר יסוד' })
    for (const r of rows.slice(1)) expect(r).toMatchObject({ value: '', choice: 'skip', status: 'missing', note: NOT_ON_PAYSLIP })
    expect(reviewFields(rows)).toEqual({ fields: { base_salary: 18000 }, errors: {} })
  })

  it('a value that contradicts the conversation has no default: the employee chooses, nothing is sent before', () => {
    const rows = reviewRows(SYNTHETIC, { base: '15000' })
    expect(rows[0]).toMatchObject({ value: '18000', choice: null, differs: true, known: '15,000 ₪' })
    expect(choicesFor(rows[0])).toEqual([{ id: 'chat', label: 'מהשיחה: 15,000 ₪' },
                                         { id: 'payslip', label: 'מהתלוש: 18,000 ₪' }])
    expect(reviewFields(rows)).toEqual({ fields: {}, errors: { base_salary: CHOOSE_FIRST } })
    const payslip = rows.map((r) => (r.key === 'base_salary' ? { ...r, choice: 'payslip' as const } : r))
    expect(reviewFields(payslip)).toEqual({ fields: { base_salary: 18000 }, errors: {} })
    const chat = rows.map((r) => (r.key === 'base_salary' ? { ...r, choice: 'chat' as const } : r))
    expect(reviewFields(chat)).toEqual({ fields: {}, errors: {} })        // the conversation value stays
  })

  it('when the conversation could not be read, nothing with a value is preselected', () => {
    const rows = reviewRows(SYNTHETIC, null)
    expect(rows[0]).toMatchObject({ value: '18000', choice: null, note: NOT_COMPARED })
    expect(rows.slice(1).every((r) => r.choice === 'skip')).toBe(true)
  })

  it('the same value as the conversation is not sent again', () => {
    const rows = reviewRows(SYNTHETIC, { base: '18000.00' })
    expect(rows[0]).toMatchObject({ same: true, differs: false, choice: 'skip' })
    expect(choicesFor(rows[0]).map((c) => c.label)).toEqual(['לעדכן', 'לא לעדכן'])
  })

  it('does not keep a low-certainty or ambiguous value', () => {
    const rows = reviewRows([f('bonus', '500', { confident: false, note: 'לא ודאי — נא לבדוק' }),
      f('pension_percent', '60', { status: 'ambiguous', confident: false, in_range: false, note: 'מחוץ לטווח' })])
    const bonus = rows.find((r) => r.key === 'bonus')!
    const pension = rows.find((r) => r.key === 'pension_percent')!
    expect(bonus).toMatchObject({ value: '500', choice: 'skip' })
    expect(pension).toMatchObject({ value: '', choice: 'skip', status: 'ambiguous' })
    expect(pension.note).toContain('60%')
  })

  it('keeps fractions: credit points 2.75', () => {
    const rows = reviewRows([f('credit_points', '2.75')])
    expect(reviewFields(rows).fields).toEqual({ credit_points: 2.75 })
  })

  it('a checked row without a valid value is an error, never sent', () => {
    const rows = reviewRows([]).map((r) => (r.key === 'children_under_23' ? { ...r, choice: 'payslip' as const, value: '2.5' } : r))
    expect(reviewFields(rows)).toEqual({ fields: {}, errors: { children_under_23: 'מספר שלם, 0 או יותר' } })
    const empty = reviewRows([]).map((r) => (r.key === 'bonus' ? { ...r, choice: 'payslip' as const } : r))
    expect(reviewFields(empty).errors.bonus).toBeDefined()
  })

  it('an unclassified addition can be assigned by the employee to an empty commissions or bonus row', () => {
    const rows = reviewRows(SYNTHETIC)
    expect(assignable(ADDITION, rows)).toEqual(['commissions', 'bonus'])
    const assigned = assign(rows, 'bonus', ADDITION)
    expect(assigned.find((r) => r.key === 'bonus')).toMatchObject({ value: '1000', choice: 'payslip', note: 'שויך על ידיכם' })
    expect(assignable(ADDITION, assigned)).toEqual(['commissions'])     // never added to a filled row
    expect(assignable({ ...ADDITION, key: 'net_pay' }, rows)).toEqual([])
  })

  it('a car copay or car maintenance can never be assigned to pay, even into empty rows (QA 22/10)', () => {
    const rows = reviewRows(SYNTHETIC)                 // commissions and bonus are empty
    const copay: PayslipInfo = { key: 'car_copay', label: 'השתתפות עובד ברכב', value: '300', kind: 'amount',
      source_label: 'השתתפות עובד ברכב', note: '...', suspect: false, assignable: false }
    expect(assignable(copay, rows)).toEqual([])
    expect(assignable({ ...copay, key: 'car_maintenance' }, rows)).toEqual([])
    // the server decides: an addition it did not mark assignable, a doubtful one, or an older server that
    // does not say, offers nothing
    expect(assignable({ ...ADDITION, assignable: false }, rows)).toEqual([])
    expect(assignable({ ...ADDITION, suspect: true }, rows)).toEqual([])
    expect(assignable({ ...ADDITION, assignable: undefined }, rows)).toEqual([])
  })

  it('the chat bubble names the base salary', () => {
    expect(profileFormText({ base_salary: 18000, bonus: 0 })).toBe('שכר בסיס 18,000 ₪, אין בונוס')
  })
})

describe('the bubble text', () => {
  it('says "ילד אחד" for one child (the value sent stays 1)', () => {
    expect(profileFormText({ children_under_23: 1 })).toBe('ילד אחד')
    expect(profileFormText({ children_under_23: 3 })).toBe('3 ילדים')
  })
})
