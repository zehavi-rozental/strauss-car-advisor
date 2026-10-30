// One inquiry, once, in the chat (stage 4): the record carries the car, the employee's question and the status; the
// representative's answer is its own message, linked and never copied; a car with an open copay inquiry gets no
// second "ask" button; a new verification link only on the latest open inquiry.
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { OptionResult, RepInquiry, ScenarioResult } from '../api/types'
import { MessageList } from './MessageList'
import type { ChatMessage } from './useConversation'

const at = '2026-10-29T10:00:00Z'
const msg = (seq: number, source: string, content: string, rep_question_id: string | null = null): ChatMessage =>
  ({ seq, role: source === 'user' ? 'user' : 'assistant', source, content, created_at: at, rep_question_id }) as ChatMessage
const inquiry = (status: RepInquiry['status'], extra: Partial<RepInquiry> = {}): RepInquiry => ({
  id: 'q1', missing_field: 'GENERAL_QUESTION', question_text: 'באיזה צבעים יש את NIRO?',
  employee_text: 'באיזה צבעים יש את NIRO?', option_id: null, car_label: 'NIRO (2025)', status,
  created_at: at, answered_at: status === 'ANSWERED' ? at : null, answer_value: null, answer_note: null, ...extra,
})
const RECORD = 'פנייה לנציג הרכב בנושא NIRO (2025):\nבאיזה צבעים יש את NIRO?\n\nהתשובה תופיע כאן בשיחה.'
const ANSWER = 'נציג התחבורה ענה על השאלה שלכם:\nלבן, שחור ואפור.'

const render = (props: Partial<Parameters<typeof MessageList>[0]>) => renderToStaticMarkup(
  <MessageList messages={[]} results={[]} resultsAnchor={null} typing={false} {...props} />)
const count = (html: string, text: string) => html.split(text).length - 1

const copayResult = (option_id: string): OptionResult => ({
  option_id, label: `${option_id} (2025)`, run_id: 'r',
  result: {
    status: 'OK', eligible: true, reason: '', missing_fields: [], today: null,
    alternative: { copay: '250' }, delta_display: '539', lines: [], cost: null, drivers: [], electric_refund: null,
    copay_pending: false, copay_source: 'catalog_final', assumptions: [], why: '', rules_version: '',
    extensions_version: '', engine_version: '',
  } as unknown as ScenarioResult,
})

describe('inquiry record in the chat', () => {
  it('shows each inquiry once, with the car, the question and the status, and links the answer', () => {
    const html = render({
      messages: [msg(1, 'user', 'באיזה צבעים יש את NIRO?'), msg(2, 'rep_inquiry', RECORD, 'q1'),
                 msg(3, 'rep', ANSWER, 'q1')],
      repQuestions: [inquiry('ANSWERED')],
    })
    expect(count(html, 'data-testid="rep-inquiry-history"')).toBe(1)
    expect(html).toContain('NIRO (2025)')
    expect(html).toContain('>נענתה<')
    expect(count(html, 'לבן, שחור ואפור.')).toBe(1)          // the answer once, in its own message
    expect(html).toContain('href="#message-3"')
    expect(html).not.toContain('התשובה תופיע כאן בשיחה')     // the "what next" line goes once it is answered
  })

  it('an open inquiry says it waits, and offers a new verification link only when asked to', () => {
    const base = { messages: [msg(2, 'rep_inquiry', RECORD, 'q1')], repQuestions: [inquiry('OPEN')] }
    const plain = render(base)
    expect(plain).toContain('ממתינה לתשובת הנציג')
    expect(plain).toContain('התשובה תופיע כאן בשיחה.')
    expect(plain).not.toContain('שליחת קישור אימות מחדש')
    expect(render({ ...base, onResendVerification: async () => 'queued' })).toContain('שליחת קישור אימות מחדש')
    const answered = render({ messages: base.messages, repQuestions: [inquiry('ANSWERED')],
                              onResendVerification: async () => 'queued' })
    expect(answered).not.toContain('שליחת קישור אימות מחדש')      // never on an answered inquiry
  })

  it("the advisor's next question is its own bubble, outside the representative's answer", () => {
    const next = 'מה קבוצת הזכאות שלכם (א-ד), או מה התפקיד שלכם?'
    const html = render({
      messages: [msg(2, 'rep_inquiry', RECORD, 'q1'), msg(3, 'rep', ANSWER, 'q1'), msg(4, 'template', next)],
      repQuestions: [inquiry('ANSWERED')],
    })
    expect(count(html, 'הנציג ענה')).toBe(1)                     // the representative's marker: once
    expect(html).toContain('href="#message-3"')                  // the record links the answer, not the question
    const answer = html.slice(html.indexOf('id="message-3"'), html.indexOf('id="message-4"'))
    expect(answer).toContain('לבן, שחור ואפור.')
    expect(answer).not.toContain(next)
    const follow = html.slice(html.indexOf('id="message-4"'))
    expect(follow).toContain(next)
    expect(follow).not.toContain('הנציג ענה')
  })

  it('an older conversation whose answer holds the follow-up too is shown as it was saved', () => {
    const legacy = `${ANSWER}\n\nמה קבוצת הזכאות שלכם?`
    const html = render({ messages: [msg(2, 'rep_inquiry', RECORD, 'q1'), msg(3, 'rep', legacy, 'q1')],
                          repQuestions: [inquiry('ANSWERED')] })
    expect(count(html, 'הנציג ענה')).toBe(1)
    expect(html).toContain('מה קבוצת הזכאות שלכם?')
    expect(html).toContain('href="#message-3"')
  })

  it('an older conversation without a record shows its question once, under the answer', () => {
    const html = render({ messages: [msg(3, 'rep', ANSWER, 'q1')], repQuestions: [inquiry('ANSWERED')] })
    expect(count(html, 'data-testid="rep-inquiry-history"')).toBe(1)
  })

  it('a car with an open copay inquiry keeps its status, without a second ask button', () => {
    const ask = () => {}
    const open = inquiry('OPEN', { missing_field: 'NEW_CAR_COPAY', option_id: 'CAR-05' })
    const withOpen = render({ results: [copayResult('CAR-05')], repQuestions: [open], onAskCopay: ask })
    const without = render({ results: [copayResult('CAR-05')], repQuestions: [], onAskCopay: ask })
    expect(without).toContain('בירור אישי מול הנציג')
    expect(withOpen).not.toContain('בירור אישי מול הנציג')
  })
})
