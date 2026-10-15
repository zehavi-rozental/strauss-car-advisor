import { describe, expect, it } from 'vitest'
import { buildFields, emptyValues, parseNumber, prefill, profileFormText } from './formFields'

describe('profileFormText (display only)', () => {
  it('writes readable values and "אין" for zero', () => {
    expect(profileFormText({ commissions: 2400, bonus: 0, pension_percent: 6, study_fund_percent: 0, credit_points: 2.75,
                             children_under_23: 2 }))
      .toBe('עמלות 2,400 ₪, אין בונוס, פנסיה 6%, אין קרן השתלמות, 2.75 נקודות זיכוי, 2 ילדים')
  })
  it('skips fields that were not sent', () => {
    expect(profileFormText({ bonus: 500 })).toBe('בונוס 500 ₪')
  })
})

describe('buildFields', () => {
  it('skips empty fields and treats 0 ("אין") as a value', () => {
    const v = { ...emptyValues(), commissions: '0', pension_percent: '6' }
    expect(buildFields(v).fields).toEqual({ commissions: 0, pension_percent: 6 })
  })
  it('sends only changed fields in a correction', () => {
    const original = { ...emptyValues(), commissions: '2400', bonus: '500' }
    const edited = { ...original, bonus: '700' }
    expect(buildFields(edited, original).fields).toEqual({ bonus: 700 })
  })
  it('reports bad input per field instead of sending it', () => {
    const r = buildFields({ ...emptyValues(), children_under_23: '2.5', bonus: '-3' })
    expect(r.fields).toEqual({})
    expect(Object.keys(r.errors).sort()).toEqual(['bonus', 'children_under_23'])
  })
})

describe('parseNumber / prefill', () => {
  it('accepts a decimal comma', () => expect(parseNumber('2,5')).toBe(2.5))
  it('rejects text and negatives', () => {
    expect(parseNumber('abc')).toBeNull()
    expect(parseNumber('-1')).toBeNull()
  })
  it('prefills known profile values and leaves unknown empty', () => {
    const v = prefill({ commissions: '2400.00', bonus: null, children: 2 })
    expect([v.commissions, v.bonus, v.children_under_23]).toEqual(['2400', '', '2'])
  })
})
