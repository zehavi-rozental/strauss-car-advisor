import { describe, expect, it } from 'vitest'
import type { ExtractedField } from '../api/types'
import { choicesFor, reviewFields, reviewRows, shownValue, typed, type ReviewRow } from './payslipReview'

// QA 22/10: after choosing "מהשיחה: 15,000 ₪" the field still showed the payslip's 18000. What the field shows,
// what is sent and what the engine calculates must always be the same value, from the source chosen.
const f = (field: ExtractedField['field'], value: string | null, extra: Partial<ExtractedField> = {}): ExtractedField => ({
  field, label: field, value, kind: 'amount', confident: true, in_range: true, note: '',
  status: value === null ? 'missing' : 'found', source_label: '', certainty: 'high', ...extra,
})
const PAYSLIP = [f('base_salary', '18000', { source_label: 'שכר יסוד' }), f('bonus', '500', { confident: false })]
const base = (rows: ReviewRow[]) => rows.find((r) => r.key === 'base_salary')!
const set = (rows: ReviewRow[], key: string, patch: Partial<ReviewRow>) =>
  rows.map((r) => (r.key === key ? { ...r, ...patch } : r))
const choose = (rows: ReviewRow[], key: string, choice: ReviewRow['choice']) => set(rows, key, { choice })
const type = (rows: ReviewRow[], key: string, text: string) =>
  rows.map((r) => (r.key === key ? { ...r, ...typed(r, text) } : r))         // exactly what the field's onChange does

describe('the field always shows the value of the source chosen', () => {
  it('chat ↔ payslip, both ways: shown = sent', () => {
    let rows = reviewRows(PAYSLIP, { base: '15000' })
    expect(shownValue(base(rows))).toBe('')                      // undecided: neither value is presented as chosen
    rows = choose(rows, 'base_salary', 'chat')
    expect(shownValue(base(rows))).toBe('15000')
    expect(reviewFields(rows).fields.base_salary).toBeUndefined()   // the conversation value stays
    rows = choose(rows, 'base_salary', 'payslip')
    expect(shownValue(base(rows))).toBe('18000')
    expect(reviewFields(rows).fields.base_salary).toBe(18000)
    rows = choose(rows, 'base_salary', 'chat')
    expect(shownValue(base(rows))).toBe('15000')
    expect(reviewFields(rows).fields.base_salary).toBeUndefined()
  })

  it('a typed value is its own explicit source, sent exactly, and never lost by switching back and forth', () => {
    let rows = reviewRows(PAYSLIP, { base: '15000' })
    rows = type(rows, 'base_salary', '16500')
    expect(base(rows).choice).toBe('manual')
    expect(shownValue(base(rows))).toBe('16500')
    expect(choicesFor(base(rows)).map((c) => c.label)).toEqual(
      ['מהשיחה: 15,000 ₪', 'מהתלוש: 18,000 ₪', 'ערך שהקלדתם: 16,500 ₪'])
    expect(reviewFields(rows).fields.base_salary).toBe(16500)
    rows = choose(rows, 'base_salary', 'payslip')
    expect(shownValue(base(rows))).toBe('18000')                 // the payslip label never shows a typed value
    expect(choicesFor(base(rows))[1].label).toBe('מהתלוש: 18,000 ₪')
    rows = choose(rows, 'base_salary', 'manual')
    expect(shownValue(base(rows))).toBe('16500')                 // what was typed is still there
    expect(reviewFields(rows).fields.base_salary).toBe(16500)
  })

  it('"לא לעדכן" shows what stays (the conversation value, or nothing), never the payslip value', () => {
    let rows = reviewRows(PAYSLIP, { bonus: '0' })
    const bonus = () => rows.find((r) => r.key === 'bonus')!
    expect(bonus().choice).toBeNull()                            // a conflict: no default, nothing presented as chosen
    expect(shownValue(bonus())).toBe('')
    expect(choicesFor(bonus()).map((c) => c.label)).toEqual(['מהשיחה: 0 ₪', 'מהתלוש: 500 ₪'])
    rows = reviewRows([f('base_salary', '18000')], { base: '18000' })
    expect(base(rows).choice).toBe('skip')                       // the same value: nothing to update
    expect(shownValue(base(rows))).toBe('18000')                 // what stays is the conversation's 18,000
    rows = reviewRows(PAYSLIP, {})
    expect(shownValue(bonus())).toBe('')
    expect(choicesFor(bonus()).map((c) => c.label)).toEqual(['לעדכן: 500 ₪', 'לא לעדכן'])
    rows = choose(rows, 'bonus', 'payslip')
    expect(shownValue(bonus())).toBe('500')
    expect(reviewFields(rows).fields.bonus).toBe(500)
  })

  it('a field not on the payslip: typing is the only way to update it', () => {
    let rows = reviewRows(PAYSLIP, {})
    const kids = () => rows.find((r) => r.key === 'children_under_23')!
    expect(choicesFor(kids()).map((c) => c.id)).toEqual(['manual', 'skip'])
    rows = type(rows, 'children_under_23', '2')
    expect(shownValue(kids())).toBe('2')
    expect(reviewFields(rows).fields.children_under_23).toBe(2)
    rows = type(rows, 'children_under_23', '')
    expect(kids().choice).toBe('skip')
    expect(reviewFields(rows).fields.children_under_23).toBeUndefined()
  })
})

describe('assigning an addition', () => {
  it('goes only into a row that is not updated and shows nothing', async () => {
    const { assign, assignable } = await import('./payslipReview')
    const addition = { key: 'other_addition', label: 'תוספת לשכר', value: '1000', kind: 'amount' as const,
                       source_label: 'תוספת חודשית', note: '', assignable: true }
    let rows = reviewRows([f('bonus', '300', { confident: false }), f('commissions', '2400')], {})
    // bonus: a payslip value offered but not chosen (empty); commissions: updated from the payslip (not empty)
    expect(assignable(addition, rows)).toEqual(['bonus'])
    rows = assign(rows, 'bonus', addition)
    const bonus = rows.find((r) => r.key === 'bonus')!
    expect([bonus.choice, shownValue(bonus)]).toEqual(['manual', '1000'])
    expect(choicesFor(bonus).map((c) => c.label)).toEqual(['לעדכן: 300 ₪', 'ערך שהקלדתם: 1,000 ₪', 'לא לעדכן'])
    expect(assignable(addition, rows)).toEqual([])                      // never into a row being updated
    expect(reviewFields(rows).fields).toEqual({ bonus: 1000, commissions: 2400 })
  })
})
