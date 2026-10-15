// Display formatting only. The server already rounds the *_display values to whole shekels.
const MINUS = '−'
const shekels = new Intl.NumberFormat('he-IL', { maximumFractionDigits: 0 })

/** "11,480 ₪"; with signed: "+661 ₪" / "−50 ₪" / "0 ₪". Accepts the server's Decimal strings. */
export function formatMoney(value: string | number, options: { signed?: boolean } = {}): string {
  const n = Math.round(Number(value))
  if (!Number.isFinite(n)) return '—'
  const abs = shekels.format(Math.abs(n))
  const sign = n < 0 ? MINUS : options.signed && n > 0 ? '+' : ''
  return `${sign}${abs} ₪`
}

/** Annual figure shown next to the monthly delta: the server's rounded monthly delta × 12.
 *  The API has no annual field yet (annual_display); this is display arithmetic only. */
export function annualFromMonthly(monthlyDisplay: string | number): number {
  return Math.round(Number(monthlyDisplay)) * 12
}
