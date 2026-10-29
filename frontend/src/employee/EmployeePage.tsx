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
import { RepInquiryCard } from './RepInquiryCard'
import { useCatalog } from './useCatalog'
import { useConversation } from './useConversation'
import { fieldsOf, linkFor, optionsField, placeholderFor } from './widgets'
import type { Expects, Message, RepQuestionIntent, RepQuestionResult } from '../api/types'

const LINK_ICON: Record<string, string> = { profile_form: 'form', selection: 'grid', payslip_upload: 'upload' }

type OpenKind = 'selection' | 'profile_form' | 'payslip_upload'

/** The advisor offered to ask the representative (a question it could not answer): the server put an ask_rep
 *  field in expects. We open the inquiry card for it instead of the old two-step choice. */
function offersAskRep(expects: Expects | null): boolean {
  return fieldsOf(expects).some((f) => f.field === 'ask_rep')
}

/** The question the employee last asked, used to pre-fill the inquiry card the advisor offered to forward. */
function lastUserQuestion(messages: Message[]): string {
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'user') return messages[i].content.trim()
  }
  return ''
}

export function EmployeePage() {
  const c = useConversation()
  const busy = c.sending || c.loading
  const [tableOpen, setTableOpen] = useState(false)
  const [mapOpen, setMapOpen] = useState(false)
  // 360°: options chosen in the map for the side-by-side table (the map itself keeps every option)
  const [mapCompare, setMapCompare] = useState<OptionResult[] | null>(null)
  const [correcting, setCorrecting] = useState(false)
  // a rep-inquiry card, opened inside the conversation (the advisor's offer, or the pre-results banner link).
  // null = closed. intent/option carry what the advisor proposed; the server re-validates.
  const [inquiry, setInquiry] = useState<{ intent: RepQuestionIntent; question: string; optionId?: string;
                                           carLabel?: string; catalogAmount?: string } | null>(null)
  const repEmail = c.repEmail ?? undefined
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
  } else if (offersAskRep(c.expects) && !inquiry) {
    // the advisor offered to ask the representative (a question it could not answer). Instead of the old
    // two-step choice, open the inquiry card inside the chat, pre-filled with the question the employee asked,
    // ready to edit and confirm — one conversation, no separate service channel.
    area = (
      <div className="reply-buttons">
        <div className="reply-row">
          <button type="button" className="chip chip-primary" disabled={busy}
                  onClick={() => setInquiry({ intent: 'general', question: lastUserQuestion(c.messages) })}>
            <Icon name="rep" size={16} />לשאול את נציג הרכב
          </button>
          <button type="button" className="chip chip-quiet" disabled={busy}
                  onClick={() => void c.send({ choice: 'skip', field: 'ask_rep' }, 'להמשיך בלי')}>
            להמשיך בלי
          </button>
        </div>
      </div>
    )
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
  // 360°: offered only when the server can build the map now (map_ready: every value and today's car known), so
  // the button never leads to "חסרים לי עוד" (QA 22/10). A map typed for earlier is still built when ready.
  const offerMap = !c.optionMap && c.mapReady
  // the explanation of catalog copay, shown before results appear (while choosing options / calculating), with a
  // gentle link to raise a copay inquiry with the rep. Derived from the stage, so it never repeats on a poll.
  const showCopayBanner = c.stage === 'CHOOSE_OPTIONS' || c.stage === 'CALCULATING'
  const openCopayInquiry = (item?: OptionResult) => {
    const car = item
    setInquiry({
      intent: 'copay_verification',
      question: car?.label
        ? `אני מבקש/ת לברר את סכום ההשתתפות החודשי האישי שלי עבור ${car.label}. האם הסכום שבקטלוג הוא הסכום שיחול עליי?`
        : 'אני מבקש/ת לברר את סכום ההשתתפות החודשי האישי שלי. האם הסכום שבקטלוג הוא הסכום שיחול עליי?',
      optionId: car?.option_id,
      carLabel: car?.label,
      catalogAmount: car?.result.alternative?.copay ?? undefined,
    })
  }
  const onInquirySaved = (res: RepQuestionResult) => {
    // reflect the just-opened question honestly: the server returned the current results and status
    if (res.status !== 'invalid') {
      setInquiry(null)
      void c.reload()
    }
  }
  const tail = (
    <div className="answer-tail" ref={tailRef}>
      {area}
      {offerMap && (
        <button type="button" className="chip map-request" disabled={busy}
                onClick={() => void c.send({ text: MAP_REQUEST }, MAP_REQUEST)}>גלו לי את כל האפשרויות</button>
      )}
      {showCopayBanner && !inquiry && (
        <div className="copay-banner" role="note">
          <Icon name="info" size={16} />
          <span>
            ההשוואה מחושבת לפי סכומי ההשתתפות שבקטלוג הרכבים של שטראוס. אם יש לכם הסדר השתתי שונה,{' '}
            <button type="button" className="link-inline" disabled={busy} onClick={() => openCopayInquiry(c.results[0])}>
              אפשר לברר מול נציג הרכב
            </button>.
          </span>
        </div>
      )}
      {inquiry && (
        <RepInquiryCard
          conversationId={c.conversationId ?? ''}
          initialQuestion={inquiry.question}
          intent={inquiry.intent}
          optionId={inquiry.optionId}
          carLabel={inquiry.carLabel}
          catalogAmount={inquiry.catalogAmount}
          knownEmail={repEmail}
          disabled={busy}
          onCancel={() => setInquiry(null)}
          onSaved={(res) => { onInquirySaved(res); }}
        />
      )}
    </div>
  )

  if (c.returnTokenPending) {
    return (
      <main className="chat-entry" dir="rtl">
        <section className="rep-inquiry-card" aria-labelledby="email-return-title">
          <header className="rep-inquiry-head"><span className="rep-inquiry-icon"><Icon name="rep" size={18} /></span>
            <h1 id="email-return-title">חזרה מאובטחת לשיחה</h1>
          </header>
          <div className="rep-inquiry-body">
            <p>כדי לפתוח את השיחה ואת תשובת נציג הרכב, אשרו את הכניסה כאן.</p>
            {c.returnTokenError && <p className="rep-inquiry-error" role="alert">{c.returnTokenError}</p>}
            <button type="button" className="btn btn-primary" disabled={c.sending}
                    onClick={() => void c.resumeEmailLink()}>
              {c.sending ? 'מאמתים…' : 'פתיחת השיחה המאובטחת'}
            </button>
          </div>
        </section>
      </main>
    )
  }

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
          {/* the interactive area stays mounted while a message is sent (disabled), so nothing typed is lost */}
          <MessageList messages={c.messages} repQuestions={c.repQuestions} results={c.results} resultsStale={c.resultsStale}
                       resultsAnchor={c.resultsAnchor} typing={c.sending}
                       onOpenTable={() => setTableOpen(true)}
                       onAskCopay={(item) => openCopayInquiry(item)}
                       mapCard={c.optionMap && <OptionMapSummary map={c.optionMap} onOpen={() => setMapOpen(true)} />}
                       tail={c.loading ? null : tail}
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
