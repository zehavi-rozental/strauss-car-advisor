import { describe, expect, it } from 'vitest'
import { annualFromMonthly, formatMoney } from './money'

describe('formatMoney', () => {
  it('formats whole shekels with thousands separators', () => {
    expect(formatMoney('11480')).toBe('11,480 ₪')
  })

  it('accepts Decimal strings with a fraction', () => {
    expect(formatMoney('661.00', { signed: true })).toBe('+661 ₪')
  })

  it('uses a true minus sign for negatives, signed or not', () => {
    expect(formatMoney('-50', { signed: true })).toBe('−50 ₪')
    expect(formatMoney(-1250)).toBe('−1,250 ₪')
  })

  it('shows zero without a sign', () => {
    expect(formatMoney('0', { signed: true })).toBe('0 ₪')
  })

  it('shows a dash for a value that is not a number', () => {
    expect(formatMoney('abc')).toBe('—')
  })
})

describe('annualFromMonthly', () => {
  it('multiplies the monthly display delta by 12', () => {
    expect(annualFromMonthly('661')).toBe(7932)
    expect(annualFromMonthly('-50')).toBe(-600)
  })
})
