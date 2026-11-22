import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { CHAT_LABEL, CHOICE_TEXT, PayslipChoice } from './PayslipChoice'
import { choiceExpanded } from './widgets'
import { failedTitle, payslipOutcome } from './payslipOutcome'

const action = { kind: 'payslip_upload' as const, label: 'להעלות תלוש שכר' }
const render = (expanded: boolean) => renderToStaticMarkup(
  <PayslipChoice action={action} expanded={expanded} disabled={false} onUpload={() => {}} onChat={() => {}} />)

describe('the two ways to give the salary details (19/11)', () => {
  it('explains both ways once, with the chat and the upload as equal choices', () => {
    const out = render(true)
    expect(out).toContain(CHOICE_TEXT)
    expect(out.replace(/&#x27;/g, "'")).toContain(CHAT_LABEL)       // the markup escapes the apostrophe
    expect(out).toContain('להעלות תלוש שכר')
    expect(out.match(/<button/g)).toHaveLength(2)
  })

  it('is one compact button afterwards, never the explanation again', () => {
    const out = render(false)
    expect(out).not.toContain(CHOICE_TEXT)
    expect(out.match(/<button/g)).toHaveLength(1)
    expect(out).toContain('להעלות תלוש שכר')
  })

  it('is expanded only while no salary value is known and the chat was not chosen', () => {
    expect(choiceExpanded([null, null], false)).toBe(true)
    expect(choiceExpanded([null, '18500'], false)).toBe(false)
    expect(choiceExpanded([null, null], true)).toBe(false)
    expect(choiceExpanded([], false)).toBe(true)
  })
})

describe('why a payslip reading failed (19/11)', () => {
  it('tells the reading service, the file and a test server apart, always as "could not read"', () => {
    for (const r of ['timeout', 'quota', 'unavailable', 'invalid_response', 'not_configured']) {
      expect(failedTitle(r)).toContain('שירות הקריאה לא זמין')
    }
    expect(failedTitle('rejected')).toContain('הקובץ לא התקבל לקריאה')
    expect(failedTitle('test_mode')).toContain('סביבת בדיקות')
    for (const r of ['timeout', 'rejected', 'test_mode', undefined]) expect(failedTitle(r)).toContain('לא הצלחנו לקרוא את התלוש')
  })

  it('carries the server message and the reason-based title into the outcome', () => {
    const out = payslipOutcome({ ok: false, status: 'failed', reason: 'test_mode', retry: false, fields: [],
                                 message_he: 'השרת הזה הוא סביבת בדיקות' } as never)
    expect(out.view).toBe('failed')
    expect(out.title).toContain('סביבת בדיקות')
    expect(out.retry).toBe(false)
  })
})
