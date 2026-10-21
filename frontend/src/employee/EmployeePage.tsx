import { useEffect, useRef, useState } from 'react'
import type { OptionResult } from '../api/types'
import { FlowCar } from '../art/FlowCar'
import { Icon } from '../ui/Icon'
import { AnswerWidgets } from './AnswerWidgets'
import { ComparisonPanel } from './ComparisonPanel'
import { OptionMapPanel, OptionMapSummary } from './OptionMapPanel'
import { MAP_REQUEST } from './optionMap'
import { Composer } from './Composer'
import { MessageList } from './MessageList'
import { PayslipUpload } from './PayslipUpload'
import { ProfileForm } from './ProfileForm'
import { Progress } from './Progress'
import { SelectionCard } from './SelectionCard'
import { payslipFormText, profileFormText } from './formFields'
import { SelectedBar } from './SummaryCard'
import { hasStoredConversation, useCatalog } from './useCatalog'
import { useConversation } from './useConversation'
import { linkFor, optionsField, placeholderFor } from './widgets'

const LINK_ICON: Record<string, string> = { profile_form: 'form', selection: 'grid', payslip_upload: 'upload' }

type OpenKind = 'selection' | 'profile_form' | 'payslip_upload'

export function EmployeePage() {
  const c = useConversation()
  const busy = c.sending || c.loading
  const [tableOpen, setTableOpen] = useState(false)
  const [mapOpen, setMapOpen] = useState(false)
  // 360°: options chosen in the map for the side-by-side table (the map itself keeps every option)
  const [mapCompare, setMapCompare] = useState<OptionResult[] | null>(null)
  const [correcting, setCorrecting] = useState(false)
  // the selection card, the profile form, and the payslip upload open only from the reply's link
  // What a link opened belongs to the question it was opened for: a new question closes it. Only a new
  // question: a poll that re-sends the same one (every 3 s while waiting for the representative) keeps the
  // selection, the form and the review open. Derived during render, so nothing stale shows for a frame.
  const [openedFor, setOpenedFor] = useState<{ kind: OpenKind; question: string } | null>(null)
  const opened = openedFor?.question === c.questionKey ? openedFor.kind : null
  const setOpened = (kind: OpenKind | null) => setOpenedFor(kind ? { kind, question: c.questionKey } : null)
  const link = linkFor(c.expects)
  const options = optionsField(c.expects)
  const formOpen = correcting || opened === 'profile_form'
  // a form, the payslip review or the cards open under the latest message: bring them into view
  const tailRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (opened || correcting) tailRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [opened, correcting])

  // The opening: until the employee writes, the stage is the whole screen (the artwork, the title and the
  // greeting with the box under it). The first message turns it into the consultation. A stored conversation
  // opens straight into the consultation, so it never flashes the opening first.
  const [resuming] = useState(hasStoredConversation)
  const intro = c.loading ? !resuming : !c.messages.some((m) => m.role === 'user')

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
                   const sent = await c.sendFields(fields, profileFormText(fields))
                   if (sent.ok) { setCorrecting(false); setOpened(null) }
                   return sent
                 }} />
  )
  const payslipUpload = (
    <PayslipUpload disabled={busy} onCancel={() => setOpened(null)} loadProfile={c.loadProfile}
                   onSubmit={async (fields) => {
                     const sent = await c.sendFields(fields, payslipFormText(fields), 'payslip')
                     if (sent.ok) setOpened(null)
                     return sent
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
  // 360°: once alternatives can be chosen, every option can be mapped instead (the request goes through the
  // chat like any message, so the server decides what is still missing)
  const offerMap = !c.optionMap && (Boolean(options) || c.results.length > 0)
  const tail = (
    <div className="answer-tail" ref={tailRef}>
      {area}
      {offerMap && (
        <button type="button" className="chip map-request" disabled={busy}
                onClick={() => void c.send({ text: MAP_REQUEST }, MAP_REQUEST)}>גלו לי את כל האפשרויות</button>
      )}
    </div>
  )

  return (
    <div className={`chat ${intro ? 'is-intro' : 'is-chat'}${c.waitingForRep ? ' is-waiting' : ''}`}>
      <FlowCar mode={intro ? 'intro' : 'band'} />
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
          {/* the interactive area stays mounted while a message is sent (disabled), so nothing typed is lost */}
          <MessageList messages={c.messages} results={c.results} resultsStale={c.resultsStale}
                       resultsAnchor={c.resultsAnchor} typing={c.sending}
                       onOpenTable={() => setTableOpen(true)}
                       mapCard={c.optionMap && <OptionMapSummary map={c.optionMap} onOpen={() => setMapOpen(true)} />}
                       tail={c.loading ? null : tail}
                       onRetry={(seq) => void c.retry(seq)} onCorrect={intro ? undefined : correct} busy={busy} />
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
          <Composer disabled={busy} placeholder={placeholderFor(c.expects)} autoFocus={intro}
                    onSend={(text) => c.send({ text }, text)} />
        </div>
      </footer>
      {tableOpen && c.results.length > 0 && (
        <ComparisonPanel results={c.results} stale={c.resultsStale} expects={c.expects} disabled={busy}
                         onClose={() => setTableOpen(false)} onSend={c.send} onCorrect={correct} details={catalog} />
      )}
      {mapOpen && c.optionMap && !mapCompare && (
        <OptionMapPanel map={c.optionMap} onClose={() => setMapOpen(false)} onCompare={setMapCompare} />
      )}
      {mapCompare && c.optionMap && (
        <ComparisonPanel results={mapCompare} stale={c.optionMap.stale} expects={c.expects} disabled={busy}
                         onClose={() => setMapCompare(null)} onSend={c.send} details={catalog}
                         onCorrect={() => { setMapCompare(null); setMapOpen(false); correct() }} />
      )}
    </div>
  )
}
