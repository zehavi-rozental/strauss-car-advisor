// One rule for an email address on every screen, the server's own (email_notifications.EMAIL_RE): a plain address
// only, so "x<victim@example.com>" is refused here before it is sent.
const EMAIL_RE = /^[a-z0-9._%+'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}$/

export const EMAIL_HINT = 'הכתובת לא נראית תקינה. בדקו אותה, למשל name@example.com.'

export function validEmail(value: string): boolean {
  const email = value.trim().toLowerCase()
  return email.length <= 254 && EMAIL_RE.test(email)
}
