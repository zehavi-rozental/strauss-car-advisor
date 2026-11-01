import { describe, expect, it } from 'vitest'
import { PROVISIONAL_NOTE, copayBannerText, engineBadge, messageAuthor } from './engineStatus'

describe('engine badge', () => {
  it('names each real state, never "available" by default', () => {
    expect(engineBadge('ai')).toMatchObject({ text: 'Gemini פעיל', tone: 'live' })
    expect(engineBadge('limited')).toMatchObject({ text: 'שירות מוגבל · מצב תבניות', tone: 'limited' })
    expect(engineBadge('none')).toMatchObject({ text: 'סביבת בדיקות ללא AI', tone: 'off' })
    expect(engineBadge('untried').tone).toBe('pending')
    expect(engineBadge(null).text).not.toContain('Gemini פעיל')
  })
})

describe('message author', () => {
  it('names the model only for what the model wrote', () => {
    expect(messageAuthor('llm')).toBe('Gemini')
    expect(messageAuthor('template')).toBe('מערכת')
    expect(messageAuthor('fallback')).toBe('מערכת')
    expect(messageAuthor('rep')).toBeNull()
  })
})

describe('copay note under the comparison', () => {
  it("names today's own amount apart from the new cars' catalog amounts", () => {
    expect(copayBannerText('200')).toContain('לרכב שלכם היום החישוב לפי ההשתתפות שמסרתם: 200 ₪')
    expect(copayBannerText('200')).toContain('ברכבים החדשים החישוב לפי סכומי ההשתתפות שבקטלוג')
    expect(copayBannerText(null)).not.toContain('לרכב שלכם היום')
    expect(copayBannerText('0')).not.toContain('לרכב שלכם היום')
  })
})

import { inquiryNumber } from '../format/inquiry'

describe('inquiry number', () => {
  it('is the same short reference on both sides, from the id', () => {
    expect(inquiryNumber('fcf708bc-c694-42b9-8138-e18b51f1b8e4')).toBe('FCF708BC')
    expect(inquiryNumber('fcf708bcc69442b98138e18b51f1b8e4')).toBe('FCF708BC')
  })
})

describe("today's copay pending with the representative (1/11)", () => {
  it("says the comparison runs on the catalog amounts and offers no second inquiry", () => {
    expect(PROVISIONAL_NOTE).toBe("ההשוואה מחושבת לפי סכומי הקטלוג. סכום ההשתתפות ברכב הנוכחי ממתין לאימות הנציג, ולאחר קבלת התשובה אפשר לעדכן את החישוב.")
    expect(PROVISIONAL_NOTE).not.toContain("לברר")
  })
})
