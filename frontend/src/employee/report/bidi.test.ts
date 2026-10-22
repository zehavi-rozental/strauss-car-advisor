import { describe, expect, it } from 'vitest'
import { fontRuns, glyphOrder, ltr, plain, visualOrder } from './bidi'

const reversed = (s: string) => Array.from(s).reverse().join('')

describe('visual order for the PDF', () => {
  it('reads Hebrew from the right', () => {
    expect(visualOrder('דוח השוואת חלופות רכב')).toBe(reversed('דוח השוואת חלופות רכב'))
  })

  it('keeps a car name and its year left to right inside Hebrew', () => {
    const v = visualOrder('הנטו עם MG4 (2025) בחודש')
    expect(v).toContain('MG4 (2025)')
    expect(v.startsWith(reversed('בחודש'))).toBe(true)
  })

  it('keeps an isolated amount exactly as on screen (digits, then ₪)', () => {
    const v = visualOrder(`יגדל ב-${ltr('1,013 ₪')} בחודש`)
    expect(v).toContain('1,013 ₪')
    expect(v).not.toMatch(/[⁦-⁩]/)
    expect(visualOrder(`בשנה ${ltr('−11,076 ₪')}`)).toContain('−11,076 ₪')
  })

  it('mirrors brackets in Hebrew', () => {
    expect(visualOrder('בשנה (דוגמה)')).toBe(`(${reversed('דוגמה')}) ${reversed('בשנה')}`)
  })

  it('splits a line between the two halves of the font and hands Hebrew runs to fontkit reversed', () => {
    const runs = fontRuns(visualOrder(`הנטו ${ltr('12,045 ₪')}`))
    expect(runs.map((r) => r.hebrew)).toEqual([false, true, false, true])
    const hebrewRun = runs.find((r) => r.hebrew && /[א-ת]/.test(r.text))!
    expect(glyphOrder(hebrewRun)).toBe(reversed(hebrewRun.text))
    const shekel = runs.find((r) => r.text === '₪')!
    expect(glyphOrder(shekel)).toBe('₪')                                     // no letters: fontkit keeps it as is
  })

  it('removes bidi controls for the metadata', () => {
    expect(plain(`ב-${ltr('300 ₪')}`)).toBe('ב-300 ₪')
  })
})
