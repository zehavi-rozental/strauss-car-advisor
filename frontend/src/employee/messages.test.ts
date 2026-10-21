import { describe, expect, it } from 'vitest'
import { bubbleText, copyableText, lastUserSeq } from './messages'
import type { ChatMessage } from './useConversation'

const msg = (seq: number, role: 'user' | 'assistant', content: string, extra: Partial<ChatMessage> = {}): ChatMessage =>
  ({ seq, role, source: role, content, created_at: '2026-10-21T10:00:00Z', ...extra })

describe('message list decisions', () => {
  it('puts "תיקון נתונים" on the last message the server has, never on a pending or failed one', () => {
    const list = [msg(1, 'assistant', 'שלום'), msg(2, 'user', 'קבוצה ג'), msg(3, 'assistant', 'רשמתי'), msg(4, 'user', 'שכר 18,500')]
    expect(lastUserSeq(list)).toBe(4)
    expect(lastUserSeq([...list, msg(-5, 'user', 'נשלח…', { pending: true })])).toBe(4)
    expect(lastUserSeq([...list, msg(-6, 'user', 'לא נשלחה', { failed: true })])).toBe(4)
    expect(lastUserSeq([msg(1, 'assistant', 'שלום')])).toBeNull()
  })

  it('shows and copies the reply without the leftover line and without bold markers', () => {
    const m = msg(3, 'assistant', 'רשמתי: **שכר בסיס 18,500 ₪**\nחסר עוד: עמלות, בונוס')
    expect(bubbleText(m)).toBe('רשמתי: **שכר בסיס 18,500 ₪**')
    expect(copyableText(m)).toBe('רשמתי: שכר בסיס 18,500 ₪')
    expect(bubbleText(msg(4, 'user', 'חסר עוד: לא שלי'))).toBe('חסר עוד: לא שלי')
  })
})
