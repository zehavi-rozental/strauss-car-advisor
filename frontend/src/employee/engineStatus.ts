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
