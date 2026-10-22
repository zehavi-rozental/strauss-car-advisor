import { useEffect, useRef, useState } from 'react'
import { FlowCar } from '../art/FlowCar'
import { Icon } from '../ui/Icon'
import { AnswerWidgets } from './AnswerWidgets'
import { ComparisonPanel } from './ComparisonPanel'
import { Composer } from './Composer'
import { MessageList } from './MessageList'
import { PayslipUpload } from './PayslipUpload'
import { ProfileForm } from './ProfileForm'
import { Progress } from './Progress'
import { SelectionCard } from './SelectionCard'
import { SelectedBar } from './SummaryCard'
import { profileFormText } from './formFields'
import { useCatalog } from './useCatalog'
import { useConversation } from './useConversation'
import { linkFor, optionsField, placeholderFor } from './widgets'

const LINK_ICON: Record<string, string> = { profile_form: 'form', selection: 'grid', payslip_upload: 'upload' }

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
  // a form, the payslip review or the cards open under the latest message: bring them into view
  const tailRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (opened || correcting) tailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [opened, correcting])

  // The consultation looks the same from the first message on (the opening artwork lives on the entry page, /).
  // Before the employee writes, the box takes the focus and there is no message to correct yet.
  const firstVisit = !c.messages.some((m) => m.role === 'user')

  // catalog details for the selection cards (display only; the cards work without them)
  const catalog = useCatalog(Boolean(options))

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
  const correct = () => { setTableOpen(false); setOpened(null); setCorrecting(true) }
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
    <PayslipUpload disabled={busy} onCancel={() => setOpened(null)} loadProfile={c.loadProfile}
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
                          onDone={() => setOpened(null)} details={catalog} />
  } else if (c.results.length > 0 && options) {
    area = <SelectedBar items={c.results} disabled={busy} onChange={() => setOpened('selection')} />
  } else {
    area = (
      <>
        <AnswerWidgets expects={c.expects} disabled={busy} onSend={c.send} />
        {link && (
          <button type="button" className={`link-button link-${link.kind}`} disabled={busy} onClick={openLink}>
            <Icon name={LINK_ICON[link.kind] ?? 'chevron'} size={18} />{link.label}
          </button>
        )}
      </>
    )
  }
  const tail = <div className="answer-tail" ref={tailRef}>{area}</div>

  return (
    <div className={`chat is-chat${c.waitingForRep ? ' is-waiting' : ''}`}>
      <FlowCar mode="band" />
      <header className="chat-header">
        <div className="chat-title">
          <h1>יועץ הרכב</h1>
          <span className="status"><i className="status-dot" />זמין עכשיו</span>
        </div>
        <Progress stage={c.stage} hasResults={c.results.length > 0} />
        <div className="chat-actions">
          {c.waitingForRep && <span className="tag tag-pending header-tag">ממתין לתשובת נציג</span>}
          <button type="button" className="new-chat" onClick={newChat} disabled={c.sending}>
            <Icon name="retry" size={16} />שיחה חדשה
          </button>
        </div>
      </header>

      <main className="chat-body">
        <div className="chat-column">
          {c.loading && (
            <p className="loading"><span className="loading-bar" aria-hidden="true" />טוען את השיחה…</p>
          )}
          <MessageList messages={c.messages} results={c.results} resultsAnchor={c.resultsAnchor} typing={c.sending}
                       onOpenTable={() => setTableOpen(true)} tail={busy ? null : tail}
                       onRetry={(seq) => void c.retry(seq)} onCorrect={firstVisit ? undefined : correct} busy={busy} />
        </div>
      </main>

      <footer className="chat-footer">
        <div className="chat-column">
          {c.error && (
            <div className="error-bar" role="alert">
              <Icon name="alert" size={18} />
              <span>{c.error}</span>
              <button type="button" onClick={c.dismissError} aria-label="סגירה"><Icon name="close" size={16} /></button>
            </div>
          )}
          <Composer disabled={busy} placeholder={placeholderFor(c.expects)} autoFocus={!c.loading && firstVisit}
                    onSend={(text) => c.send({ text }, text)} />
        </div>
      </footer>
      {tableOpen && c.results.length > 0 && (
        <ComparisonPanel results={c.results} expects={c.expects} disabled={busy} onClose={() => setTableOpen(false)}
                         onSend={c.send} onCorrect={correct} details={catalog} />
      )}
    </div>
  )
}
