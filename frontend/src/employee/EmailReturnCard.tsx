import { Icon } from '../ui/Icon'

/** The page an email link opens. Confirming redeems the one-time token. A link the server refused for good (used
 *  or expired) is never offered again: the card then offers the conversation stored in this browser, when there
 *  is one (the server still checks this browser's session), or a new conversation. */
export function EmailReturnCard({ error, invalid, canReturn, sending, onOpen, onNew }: {
  error: string | null
  invalid: boolean          // the token was refused and dropped
  canReturn: boolean        // a conversation is stored in this browser
  sending: boolean
  onOpen: () => void        // redeems the token, or (after a refusal) opens the stored conversation
  onNew: () => void
}) {
  return (
    <main className="chat-entry" dir="rtl">
      <section className="rep-inquiry-card" aria-labelledby="email-return-title">
        <header className="rep-inquiry-head"><span className="rep-inquiry-icon"><Icon name="rep" size={18} /></span>
          <h1 id="email-return-title">חזרה מאובטחת לשיחה</h1>
        </header>
        <div className="rep-inquiry-body">
          {!invalid && <p>כדי לפתוח את השיחה ואת תשובת נציג הרכב, אשרו את הכניסה כאן.</p>}
          {error && <p className="rep-inquiry-error" role="alert">{error}</p>}
          {!invalid ? (
            <button type="button" className="btn btn-primary" disabled={sending} onClick={onOpen}>
              {sending ? 'מאמתים…' : 'פתיחת השיחה המאובטחת'}
            </button>
          ) : canReturn ? (
            <button type="button" className="btn btn-primary" disabled={sending} onClick={onOpen}>
              {sending ? 'פותחים…' : 'חזרה לשיחה'}
            </button>
          ) : (
            <button type="button" className="btn btn-quiet" disabled={sending} onClick={onNew}>שיחה חדשה</button>
          )}
        </div>
      </section>
    </main>
  )
}
