import { useState } from 'react'
import { ComparisonPanel } from './ComparisonPanel'
import { Composer } from './Composer'
import { MessageList } from './MessageList'
import { ProfileForm } from './ProfileForm'
import { Q2_FIELD_NAMES, profileFormText } from './formFields'
import { QuickReplies } from './QuickReplies'
import { useConversation } from './useConversation'

function CarIcon() {
  return (
    <svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">
      <path fill="currentColor"
            d="M5 11l1.5-4.5A2 2 0 0 1 8.4 5h7.2a2 2 0 0 1 1.9 1.5L19 11h.5A1.5 1.5 0 0 1 21 12.5V17h-2v1.5a1.5 1.5 0 0 1-3 0V17H8v1.5a1.5 1.5 0 0 1-3 0V17H3v-4.5A1.5 1.5 0 0 1 4.5 11H5Zm2.1 0h9.8l-1.2-3.6a.6.6 0 0 0-.6-.4H8.9a.6.6 0 0 0-.6.4L7.1 11ZM6.5 15a1 1 0 1 0 0-2 1 1 0 0 0 0 2Zm11 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" />
    </svg>
  )
}

export function EmployeePage() {
  const c = useConversation()
  const busy = c.sending || c.loading
  const [tableOpen, setTableOpen] = useState(false)
  const [correcting, setCorrecting] = useState(false)
  // Q2 arrives as several profile fields at once: show it as a form
  const q2Fields = c.expects?.fields.filter((f) => f.type === 'number' && f.field !== null && Q2_FIELD_NAMES.has(f.field)) ?? []
  const showQ2 = q2Fields.length >= 2 && !correcting
  const newChat = () => {
    if (window.confirm('להתחיל שיחה חדשה?')) {
      setTableOpen(false)
      setCorrecting(false)
      void c.newConversation()
    }
  }
  return (
    <div className="chat">
      <header className="chat-header">
        <span className="avatar"><CarIcon /></span>
        <div className="chat-title">
          <h1>יועץ הרכב</h1>
          <span className="status"><i className="status-dot" />זמין עכשיו</span>
        </div>
        <button type="button" className="new-chat" onClick={newChat} disabled={c.sending}>שיחה חדשה</button>
        {c.waitingForRep && <span className="tag tag-pending header-tag">ממתין לתשובת נציג</span>}
      </header>

      <main className="chat-body">
        {c.loading && <p className="loading">טוען את השיחה…</p>}
        <MessageList messages={c.messages} results={c.results} resultsAnchor={c.resultsAnchor} typing={c.sending}
                     onOpenTable={() => setTableOpen(true)} />
      </main>

      <footer className="chat-footer">
        {c.error && (
          <div className="error-bar" role="alert">
            <span>{c.error}</span>
            <button type="button" onClick={c.dismissError} aria-label="סגירה">×</button>
          </div>
        )}
        {(showQ2 || correcting) && (
          <ProfileForm mode={correcting ? 'correction' : 'q2'} loadProfile={c.loadProfile} disabled={busy}
                       onCancel={correcting ? () => setCorrecting(false) : undefined}
                       onSubmit={async (fields) => {
                         const ok = await c.send({ fields }, profileFormText(fields))
                         if (ok) setCorrecting(false)
                         return ok
                       }} />
        )}
        <QuickReplies expects={c.expects} disabled={busy} onSend={c.send} hideNumbers={showQ2 || correcting} />
        <Composer disabled={busy} onSend={(text) => c.send({ text }, text)} />
      </footer>
      {tableOpen && c.results.length > 0 && (
        <ComparisonPanel results={c.results} expects={c.expects} disabled={busy} onClose={() => setTableOpen(false)}
                         onSend={c.send} onCorrect={() => { setTableOpen(false); setCorrecting(true) }} />
      )}
    </div>
  )
}
