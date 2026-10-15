import { describe, expect, it } from 'vitest'
import { checkMoney, relativeTime } from './repForm'

describe('checkMoney', () => {
  it('accepts whole and 2-digit amounts', () => {
    expect(checkMoney('350')).toEqual({ ok: true, value: '350' })
    expect(checkMoney('1,250.50')).toEqual({ ok: true, value: '1250.50' })
  })
  it('rejects negative, 3 decimals, text and empty', () => {
    for (const bad of ['-5', '1.234', 'abc', ' ']) expect(checkMoney(bad).ok).toBe(false)
  })
})

describe('relativeTime', () => {
  const now = Date.parse('2026-10-15T12:00:00Z')
  it('speaks in minutes, hours and days', () => {
    expect(relativeTime('2026-10-15T11:59:50Z', now)).toBe('הרגע')
    expect(relativeTime('2026-10-15T11:55:00Z', now)).toBe('לפני 5 דקות')
    expect(relativeTime('2026-10-15T10:00:00Z', now)).toBe('לפני 2 שעות')
    expect(relativeTime('2026-10-13T12:00:00Z', now)).toBe('לפני 2 ימים')
  })
})
