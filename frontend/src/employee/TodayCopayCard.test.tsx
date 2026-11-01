import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { ExpectsField, OptionResult, RepInquiry, ScenarioResult } from '../api/types'
import { MessageList } from './MessageList'
import { RepInquiryCard } from './RepInquiryCard'
import { CATALOG_EXPLAIN, TodayCopayCard, type TodayCopayState } from './TodayCopayCard'
import type { ChatMessage } from './useConversation'

const base: TodayCopayState = { car: 'COROLLA (2026)', copay: null, source: null, provisional: null, inquiryOpen: false }
const card = (s: Partial<TodayCopayState>) =>
  renderToStaticMarkup(<TodayCopayCard state={{ ...base, ...s }} onAsk={() => {}} />)
const count = (html: string, text: string) => html.split(text).length - 1

describe("today's car above the comparison (1/11)", () => {
  it('the catalog amount the employee took: car, amount, source, the explanation once and the ask button', () => {
    const out = card({ copay: '250', source: 'catalog_confirmed' })
    expect(out).toContain('COROLLA (2026)')
    expect(out).toContain('250')
    expect(out).toContain('סכום הקטלוג, שאישרתם שהוא הסכום שלכם')
    expect(count(out, CATALOG_EXPLAIN)).toBe(1)
    expect(out).toContain('בירור השתתפות מול נציג')
  })

  it('a provisional amount with an open inquiry: one provisional mark, the status, no second ask button', () => {
    const out = card({ provisional: '250', inquiryOpen: true, inquiryHref: '#message-9' })
    expect(count(out, 'זמני')).toBeGreaterThanOrEqual(1)
    expect(out).toContain('נשלחה פנייה לנציג התחבורה')
    expect(out).not.toContain('בירור השתתפות מול נציג')
    expect(out).not.toContain('אושר')
  })

  it('an answer waiting: says so with a link to it, never "waiting for the representative"', () => {
    const out = card({ provisional: '250', inquiryOpen: false, answerAmount: '300', answerHref: '#message-12' })
    expect(out).toContain('התקבלה תשובת הנציג')
    expect(out).toContain('href="#message-12"')
    expect(out).not.toContain('נשלחה פנייה')
    expect(out).not.toContain('בירור השתתפות')
  })

  it("the representative's applied amount and a typed amount are personal: no catalog explanation, no button", () => {
    for (const out of [card({ copay: '300', source: 'rep_confirmed' }), card({ copay: '200' })]) {
      expect(out).not.toContain(CATALOG_EXPLAIN)
      expect(out).not.toContain('בירור השתתפות מול נציג')
    }
    expect(card({ copay: '300', source: 'rep_confirmed' })).toContain('אושר על ידי הנציג')
    expect(card({ copay: '200' })).toContain('הסכום שמסרתם')
  })

  it('no amount in use: nothing is shown (never an invented amount)', () => {
    expect(card({})).toBe('')
  })
})

const at = '2026-11-01T10:00:00Z'
const msg = (seq: number, source: string, content: string, rep_question_id: string | null = null): ChatMessage =>
  ({ seq, role: 'assistant', source, content, created_at: at, rep_question_id }) as ChatMessage
const answered: RepInquiry = {
  id: 'q7', missing_field: 'CURRENT_COPAY', question_text: '', employee_text: 'מה ההשתתפות שלי?',
  option_id: 'today:CAR-07', car_label: 'COROLLA (2026)', status: 'ANSWERED', created_at: at, answered_at: at,
  answer_value: '300', answer_note: 'לפי ההסכם האישי',
}
const offer = { field: 'rep_copay', type: 'choice', quick_replies: [], max: null, closed: true,
                choices: [{ id: 'apply:q7', label: 'עדכון הסכום וחישוב מחדש' }],
                context: { car: 'COROLLA (2026)', amount: '300', previous: null, provisional: '250' } } as unknown as ExpectsField
const result: OptionResult = {
  option_id: 'MG4', label: 'MG4 (2025)', run_id: 'r',
  result: { status: 'OK', eligible: true, reason: '', missing_fields: [], today: null, alternative: { copay: '300' },
            delta_display: '500', lines: [], cost: null, drivers: [], electric_refund: null, copay_pending: true,
            copay_source: 'catalog', today_copay_provisional: '250', assumptions: [], why: '', rules_version: '',
            extensions_version: '', engine_version: '' } as unknown as ScenarioResult,
}
const list = (props: Partial<Parameters<typeof MessageList>[0]>) => renderToStaticMarkup(
  <MessageList messages={[msg(3, 'rep', 'נציג התחבורה השיב: 300', 'q7')]} repQuestions={[answered]} results={[]}
               resultsAnchor={null} typing={false} onSend={async () => true} {...props} />)

describe("the representative's answer about today's copay, one unit (1/11)", () => {
  it('title, car, the amount in use, the amount given, the note, and the update button once', () => {
    const out = list({ repCopayOffer: offer })
    expect(out).toContain('התקבלה תשובת נציג התחבורה')
    expect(out).toContain('COROLLA (2026)')
    expect(out).toContain('הסכום בחישוב הנוכחי')
    expect(out).toContain('250')
    expect(out).toContain('הסכום שהנציג מסר')
    expect(out).toContain('לפי ההסכם האישי')
    expect(count(out, 'עדכון הסכום וחישוב מחדש')).toBe(1)
    expect(out).not.toContain('נציג התחבורה השיב')        // the bubble text is not said twice
  })

  it('after the update: no button, a confirmation with the link to the results', () => {
    const out = list({ repCopayOffer: null, todayCopay: '300', todayCopaySource: 'rep_confirmed', resultsAnchor: 3,
                       results: [result] })
    expect(out).not.toContain('עדכון הסכום וחישוב מחדש')
    expect(out).toContain('החישוב עודכן')
    expect(out).toContain('לתוצאות המעודכנות')
  })

  it("the cards no longer repeat today's provisional amount; the card above says it once", () => {
    const out = list({ results: [result], resultsAnchor: 3, todayCard: <p className="today-card-slot">today</p> })
    expect(out).not.toContain('מחושב לפי סכום קטלוגי זמני')
    expect(count(out, 'today-card-slot')).toBe(1)
    expect(out.indexOf('today-card-slot')).toBeLessThan(out.indexOf('MG4'))
  })
})

describe('the inquiry form (1/11)', () => {
  it('the address field is visible before "המשך", above the message', () => {
    const out = renderToStaticMarkup(
      <RepInquiryCard conversationId="c1" initialQuestion="מה ההשתתפות שלי?" intent="current_copay"
                      carLabel="COROLLA (2026)" catalogAmount="250" emailEnabled onCancel={() => {}} onSaved={() => {}} />)
    const email = out.indexOf('type="email"'), message = out.indexOf('<textarea'), next = out.indexOf('המשך')
    expect(email).toBeGreaterThan(out.indexOf('COROLLA (2026)'))
    expect(email).toBeLessThan(message)
    expect(message).toBeLessThan(next)
  })
})
