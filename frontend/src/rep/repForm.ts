// Pure helpers for the rep screen: money validation and relative time.
export type MoneyCheck = { ok: true; value: string } | { ok: false; message: string }

/** MONEY answer: a number, not negative, at most 2 digits after the point. The server checks it again. */
export function checkMoney(text: string): MoneyCheck {
  const t = text.trim().replace(/,/g, '')
  if (!t) return { ok: false, message: 'יש להזין סכום' }
  if (!/^-?\d+(\.\d+)?$/.test(t)) return { ok: false, message: 'הסכום חייב להיות מספר' }
  if (t.startsWith('-')) return { ok: false, message: 'הסכום לא יכול להיות שלילי' }
  if ((t.split('.')[1] ?? '').length > 2) return { ok: false, message: 'עד שתי ספרות אחרי הנקודה' }
  return { ok: true, value: t }
}

/** "לפני 5 דקות" / "לפני 2 שעות" / "הרגע" */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const s = Math.max(0, Math.round((now - t) / 1000))
  if (s < 60) return 'הרגע'
  const m = Math.floor(s / 60)
  if (m < 60) return m === 1 ? 'לפני דקה' : `לפני ${m} דקות`
  const h = Math.floor(m / 60)
  if (h < 24) return h === 1 ? 'לפני שעה' : `לפני ${h} שעות`
  const d = Math.floor(h / 24)
  return d === 1 ? 'לפני יום' : `לפני ${d} ימים`
}
