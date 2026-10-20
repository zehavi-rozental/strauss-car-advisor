import { useEffect, useState } from 'react'
import { ComparisonPanel } from './ComparisonPanel'
import { Composer } from './Composer'
import { MessageList } from './MessageList'
import { ProfileForm } from './ProfileForm'
import { PayslipUpload } from './PayslipUpload'
import { AnswerWidgets } from './AnswerWidgets'
import { Progress } from './Progress'
import { SelectionCard } from './SelectionCard'
import { profileFormText } from './formFields'
import { SelectedBar } from './SummaryCard'
import { linkFor, optionsField, placeholderFor } from './widgets'
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
  // the selection card, the profile form, and the payslip upload open only from the reply's link
  const [opened, setOpened] = useState<'selection' | 'profile_form' | 'payslip_upload' | null>(null)
  useEffect(() => setOpened(null), [c.expects])        // a new question closes what the last one opened
  const link = linkFor(c.expects)
  const options = optionsField(c.expects)
  const formOpen = correcting || opened === 'profile_form'
  // a pilot can point the link at an external upload page; then the link just opens it in a new tab
  const openLink = () => {
    if (link?.kind === 'payslip_upload' && link.url) {
      window.open(link.url, '_blank', 'noopener,noreferrer')
      return
    }
    if (link) setOpened(link.kind)
  }
  const newChat = () => {
    if (window.confirm('להתחיל שיחה חדשה?')) {
      setTableOpen(false)
      setCorrecting(false)
      setOpened(null)
      void c.newConversation()
    }
  }
  const profileForm = (
    <ProfileForm mode={correcting ? 'correction' : 'q2'} loadProfile={c.loadProfile} disabled={busy}
                 onCancel={() => { setCorrecting(false); setOpened(null) }}
                 onSubmit={async (fields) => {
                   const ok = await c.send({ fields }, profileFormText(fields))
                   if (ok) { setCorrecting(false); setOpened(null) }
                   return ok
                 }} />
  )
  const payslipUpload = (
    <PayslipUpload disabled={busy} onCancel={() => setOpened(null)}
                   onSubmit={async (fields) => {
                     const ok = await c.send({ fields }, profileFormText(fields))
                     if (ok) setOpened(null)
                     return ok
                   }} />
  )
  // only one interactive area at a time: the open form / selection / payslip upload, or the closed
  // question's buttons, or after results the collapsed selection line
  let area = null
  if (formOpen) area = profileForm
  else if (opened === 'payslip_upload') area = payslipUpload
  else if (opened === 'selection' && options) {
    area = <SelectionCard f={options} disabled={busy} onSend={c.send} initial={c.results.map((r) => r.option_id)}
                          onDone={() => setOpened(null)} />
  } else if (c.results.length > 0 && options) {
    area = <SelectedBar items={c.results} disabled={busy} onChange={() => setOpened('selection')} />
  } else {
    area = (
      <>
        <AnswerWidgets expects={c.expects} disabled={busy} onSend={c.send} />
        {link && (
          <button type="button" className="link-button" disabled={busy} onClick={openLink}>
            {link.label}
          </button>
        )}
      </>
    )
  }
  const tail = <div className="answer-tail">{area}</div>

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
        <Progress stage={c.stage} hasResults={c.results.length > 0} />
      </header>

      <main className="chat-body">
        {c.loading && <p className="loading">טוען את השיחה…</p>}
        <MessageList messages={c.messages} results={c.results} resultsAnchor={c.resultsAnchor} typing={c.sending}
                     onOpenTable={() => setTableOpen(true)}
                     tail={busy ? null : tail} />
      </main>

      <footer className="chat-footer">
        {c.error && (
          <div className="error-bar" role="alert">
            <span>{c.error}</span>
            <button type="button" onClick={c.dismissError} aria-label="סגירה">×</button>
          </div>
        )}
        <Composer disabled={busy} placeholder={placeholderFor(c.expects)} onSend={(text) => c.send({ text }, text)} />
      </footer>
      {tableOpen && c.results.length > 0 && (
        <ComparisonPanel results={c.results} expects={c.expects} disabled={busy} onClose={() => setTableOpen(false)}
                         onSend={c.send} onCorrect={() => { setTableOpen(false); setCorrecting(true) }} />
      )}
    </div>
  )
}
