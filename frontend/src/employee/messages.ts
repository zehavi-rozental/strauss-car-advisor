// What the message list needs to decide, as pure functions (no markup, no style): the bubble text, the text a
// copy puts on the clipboard, and which message carries "תיקון נתונים".
import type { ChatMessage } from './useConversation'

/** The compact "חסר עוד" row replaces this server line, so the bubble does not repeat it. */
export const withoutLeftoverTail = (text: string) =>
  text.split('\n').filter((l) => !l.startsWith('חסר עוד:')).join('\n').trim()

/** The text of a bubble as shown (assistant replies without the leftover line). */
export const bubbleText = (m: ChatMessage) => (m.role === 'assistant' ? withoutLeftoverTail(m.content) : m.content)

/** What "העתקה" copies: the bubble text without the markdown bold markers. */
export const copyableText = (m: ChatMessage) => bubbleText(m).replace(/\*\*/g, '')

/** "תיקון נתונים" sits on the employee's latest message that the server has (never on one pending or not sent). */
export function lastUserSeq(messages: ChatMessage[]): number | null {
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i]
    if (m.role === 'user' && !m.pending && !m.failed) return m.seq
  }
  return null
}

/** Puts text on the clipboard; true when it worked. */
export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // older browsers / no permission: the classic way
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    area.remove()
    return ok
  }
}
