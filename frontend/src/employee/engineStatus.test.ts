import { describe, expect, it } from 'vitest'
import { engineBadge } from './engineStatus'

describe('engine badge', () => {
  it('names each real state, never "available" by default', () => {
    expect(engineBadge('ai')).toMatchObject({ text: 'Gemini פעיל', tone: 'live' })
    expect(engineBadge('limited')).toMatchObject({ text: 'שירות מוגבל · מצב תבניות', tone: 'limited' })
    expect(engineBadge('none')).toMatchObject({ text: 'סביבת בדיקות ללא AI', tone: 'off' })
    expect(engineBadge('untried').tone).toBe('pending')
    expect(engineBadge(null).text).not.toContain('Gemini פעיל')
  })
})

import { inquiryNumber } from '../format/inquiry'

describe('inquiry number', () => {
  it('is the same short reference on both sides, from the id', () => {
    expect(inquiryNumber('fcf708bc-c694-42b9-8138-e18b51f1b8e4')).toBe('FCF708BC')
    expect(inquiryNumber('fcf708bcc69442b98138e18b51f1b8e4')).toBe('FCF708BC')
  })
})
