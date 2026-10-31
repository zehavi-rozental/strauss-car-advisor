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
