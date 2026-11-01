// The header badge: what really answers the conversation (31/10). The state comes from the server — the provider
// chain's real status at load, then what answered each turn — never from a key being configured.
import type { EngineState } from '../api/types'

export interface EngineBadge {
  text: string
  tone: 'live' | 'pending' | 'limited' | 'off'
  title: string
}

export function engineBadge(state: EngineState | null): EngineBadge {
  switch (state) {
    case 'ai':
      return { text: 'Gemini פעיל', tone: 'live', title: 'התשובות נכתבות בעזרת Gemini; החישובים נעשים במנוע החישוב.' }
    case 'untried':
      return { text: 'Gemini מוגדר · ממתין להודעה', tone: 'pending',
               title: 'Gemini יופעל בהודעה הבאה שצריכה הבנה של טקסט חופשי.' }
    case 'limited':
      return { text: 'שירות מוגבל · מצב תבניות', tone: 'limited',
               title: 'Gemini לא ענה (תקלה או מכסה). התשובות מגיעות מתבניות קבועות, והחישובים לא נפגעים.' }
    case 'none':
      return { text: 'סביבת בדיקות ללא AI', tone: 'off', title: 'אין מנוע שפה בסביבה הזו: תבניות קבועות בלבד.' }
    default:
      return { text: 'בודק את מצב השירות', tone: 'pending', title: '' }
  }
}

/** Who wrote one bot message (31/10): the model, or the system (a template, code, a fixed message). The rep's own
 * answers carry their event pill instead. Shown only where a model is configured, so a no-AI environment is not
 * labelled message by message. */
export function messageAuthor(source: string): string | null {
  if (source === 'llm') return 'Gemini'
  if (source === 'rep') return null
  return 'מערכת'
}

/** The note under the comparison (31/10): the NEW cars use the catalog amounts; today's car uses the amount the
 * employee gave or confirmed — said apart, so a typed amount never reads as still to be checked. */
const TODAY_SOURCE: Record<string, string> = {
  rep_confirmed: 'לפי תשובת הנציג שאישרתם', catalog_confirmed: 'לפי סכום הקטלוג שאישרתם',
}

export function copayBannerText(todayCopay: string | null, source: string | null = null): string {
  const words = (source && TODAY_SOURCE[source]) || 'שמסרתם'
  const today = todayCopay && Number(todayCopay) > 0
    ? `. לרכב שלכם היום החישוב לפי ההשתתפות ${words}: ${Number(todayCopay).toLocaleString('he-IL')} ₪`
    : ''
  return `ברכבים החדשים החישוב לפי סכומי ההשתתפות שבקטלוג של שטראוס${today}. אם לרכב חדש סוכם אתכם סכום אחר,`
}

/** Today's copay pending with the representative (1/11): the comparison runs on the catalog amounts meanwhile; no
 * second inquiry is offered while this one is open. */
export const PROVISIONAL_NOTE = 'ההשוואה מחושבת לפי סכומי הקטלוג. סכום ההשתתפות ברכב הנוכחי ממתין לאימות הנציג, ולאחר קבלת התשובה אפשר לעדכן את החישוב.'
