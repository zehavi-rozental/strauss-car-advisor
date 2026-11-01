import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import type { OptionResult } from '../api/types'
import { FlowCar } from '../art/FlowCar'
import { Icon } from '../ui/Icon'
import { AnswerWidgets } from './AnswerWidgets'
import { ComparisonPanel } from './ComparisonPanel'
import { EmailReturnCard } from './EmailReturnCard'
import { engineBadge } from './engineStatus'
import { OptionMapPanel, OptionMapSummary } from './OptionMapPanel'
import { MAP_REQUEST } from './optionMap'
import { Composer } from './Composer'
import { MessageList } from './MessageList'
import { PayslipUpload } from './PayslipUpload'
import { TodayCopayCard } from './TodayCopayCard'
import { ProfileForm } from './ProfileForm'
import { Progress } from './Progress'
import { SelectionCard } from './SelectionCard'
import { payslipFormText, profileFormText } from './formFields'
import { CarActions } from './CarActions'
import { RepInquiryCard } from './RepInquiryCard'
import { useCatalog } from './useCatalog'
import { useConversation } from './useConversation'
import { fieldsOf, linkFor, optionsField, placeholderFor } from './widgets'
import type { Expects, Message, RepQuestionIntent, RepQuestionResult } from '../api/types'

const LINK_ICON: Record<string, string> = { profile_form: 'form', selection: 'grid', payslip_upload: 'upload',
                                            correction_form: 'edit' }

type OpenKind = 'selection' | 'profile_form' | 'payslip_upload' | 'correction_form'

/** The advisor offered to ask the representative (a question it could not answer): the server put an ask_rep
 *  field in expects. We open the inquiry card for it instead of the old two-step choice. */
function offersAskRep(expects: Expects | null): boolean {
  return fieldsOf(expects).some((f) => f.field === 'ask_rep')
}

/** The question the advisor offers to forward: the server's (the open question, never "תשאל את הנציג" itself);
 *  an older server does not say, so the employee's last message. */
function offeredQuestion(expects: Expects | null, messages: Message[]): string {
  const field = fieldsOf(expects).find((f) => f.field === 'ask_rep')
  return field && field.question !== undefined ? field.question ?? '' : lastUserQuestion(messages)
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
  // `opened` counts every opening: the card is remounted each time (its key), so text typed for one car never stays
  // in the field when the card is opened for another one (the NIRO inquiry that carried a DOLPHIN question)
  const [inquiry, setInquiryState] = useState<{ intent: RepQuestionIntent; question: string; optionId?: string;
                                                carLabel?: string; catalogAmount?: string; copayToday?: boolean; subject?: string;
                                                opened: number } | null>(null)
  const inquiryCount = useRef(0)
  const setInquiry = (next: Omit<NonNullable<typeof inquiry>, 'opened'> | null) =>
    setInquiryState(next && { ...next, opened: ++inquiryCount.current })
  // "לברר מול נציג" on today's copay (31/10): a draft only; the copay is marked as pending with the rep once the
  // inquiry was really sent (created / already open), never by opening the card
  const openTodayCopayInquiry = (car?: string, amount?: string) => setInquiry({
    intent: 'current_copay', copayToday: true, carLabel: car, catalogAmount: amount,
    subject: 'בירור סכום ההשתתפות ברכב הנוכחי',
    question: `אני מבקש/ת לברר מה סכום ההשתתפות החודשי שלי ברכב הנוכחי${car ? ` (${car})` : ''}. ` +
      'האם הסכום שבקטלוג הוא הסכום שחל עליי?',
  })
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
  // "אני רוצה לתקן נתון" (1/11): the reply's link opens the same correction form as "תיקון נתונים"
  const fixing = correcting || opened === 'correction_form'
  const formOpen = fixing || opened === 'profile_form'
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
    <ProfileForm mode={fixing ? 'correction' : 'q2'} loadProfile={c.loadProfile} disabled={busy}
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
                  onClick={() => setInquiry({ intent: 'general', question: offeredQuestion(c.expects, c.messages) })}>
            <Icon name="rep" size={16} />לשאול את נציג הרכב
          </button>
          <button type="button" className="chip chip-quiet" disabled={busy}
                  onClick={() => void c.send({ choice: 'skip', field: 'ask_rep' }, 'להמשיך בלי')}>
            להמשיך בלי
          </button>
        </div>
      </div>
    )
  } else {
    area = (
      <>
        <AnswerWidgets expects={c.expects} disabled={busy} onSend={c.send} onCopayRep={openTodayCopayInquiry} />
        {link && link.kind !== 'selection' && (
          <button type="button" className={`link-button link-${link.kind}`} disabled={busy} onClick={openLink}>
            <Icon name={LINK_ICON[link.kind] ?? 'chevron'} size={18} />{link.label}
          </button>
        )}
      </>
    )
  }
  // the cars, one compact place (1/11): choose for the chat's comparison, the 3 with the highest net, or every car
  // by net (the map, to look at). Shown with the alternatives question and after results, while no closed question
  // or open form/card needs the answer area; the map's entry says what is missing instead of hiding.
  const rank = options?.quick_replies.find((q) => q.id === 'rank') ?? null
  const closedAsked = fieldsOf(c.expects).some((f) => f.closed && f.field !== 'rep_copay')
  // the alternatives field rides along with the car question; the cars' place opens once the alternatives are asked
  const choosing = options != null && (c.stage === 'CHOOSE_OPTIONS' || c.results.length > 0)
  const showCarActions = !formOpen && !opened && !inquiry && !offersAskRep(c.expects) && !closedAsked
    && (choosing || c.results.length > 0 || c.optionMap != null)
  const carActions = showCarActions && (
    <CarActions selected={c.resultsStale ? [] : c.results.map((r) => r.label)} canChoose={choosing}
                canRank={rank != null} mapReady={c.mapReady} mapExists={c.optionMap != null} mapMissing={c.mapMissing}
                disabled={busy} onChoose={() => setOpened('selection')}
                onRank={() => { if (rank) void c.send({ choice: rank.id, field: 'options' }, rank.label) }}
                onMap={() => (c.optionMap ? setMapOpen(true) : void c.send({ text: MAP_REQUEST }, MAP_REQUEST))}
                onCompleteData={correct} />
  )
  // the explanation of catalog copay, shown before results appear (while choosing options / calculating), with a
  // gentle link to raise a copay inquiry with the rep. Derived from the stage, so it never repeats on a poll.
  // the representative's amount for today's copay, waiting for the employee: its button is under the answer (1/11)
  const repCopayOffer = fieldsOf(c.expects).find((f) => f.field === 'rep_copay') ?? null
  // today's copay inquiry and its answer, by the exact question (the option id "today:<code>")
  const todayQuestions = c.repQuestions.filter((q) => q.missing_field === 'CURRENT_COPAY' && q.option_id?.startsWith('today:'))
  const openToday = todayQuestions.find((q) => q.status === 'OPEN')
  const offerId = repCopayOffer?.choices[0]?.id?.replace(/^apply:/, '') ?? null
  const answerMessage = offerId ? c.messages.find((m) => m.source === 'rep' && m.rep_question_id === offerId) : undefined
  const recordMessage = openToday ? c.messages.find((m) => m.source === 'rep_inquiry' && m.rep_question_id === openToday.id) : undefined
  const todayCard = c.results.length > 0 && (
    <TodayCopayCard disabled={busy} onAsk={openTodayCopayInquiry} state={{
      car: c.todayCar, copay: c.todayCopay, source: c.todayCopaySource, provisional: c.todayCopayProvisional,
      inquiryOpen: openToday != null, inquiryHref: recordMessage ? `#message-${recordMessage.seq}` : null,
      answerAmount: repCopayOffer ? repCopayOffer.context?.amount ?? null : null,
      answerHref: answerMessage ? `#message-${answerMessage.seq}` : null,
    }} />
  )
  const onInquirySaved = (res: RepQuestionResult) => {
    // the copay question on screen is answered "with the representative"; after results the amount in use stays
    // as it is (the card shows the open inquiry) and the answer is offered as an update
    const copayAsked = fieldsOf(c.expects).some((f) => f.field === 'copay')
    if (inquiry?.copayToday && copayAsked && (res.status === 'created' || res.status === 'exists')) {
      void c.send({ choice: 'rep', field: 'copay' }, 'לברר מול נציג')
    }
    // created: the inquiry's record now stands in the chat, so the card closes. exists: the card stays and says
    // so, with a link to the record already in the chat (nothing new was sent)
    if (res.status === 'created') setInquiry(null)
    // the employee stays at the inquiry (1/11): its record is brought into view and kept there through the polls,
    // never the comparison; shown only once the server saved it (this runs after its answer)
    if (res.status === 'created' && res.question_id) c.focusQuestion(res.question_id)
    void c.reload()
  }
  // a new verification link, offered on the latest open inquiry only while mail is on and the address waits
  const resendVerification = c.emailEnabled && c.emailPending && !repEmail && c.conversationId
    ? async () => {
        try {
          const res = await api.resendVerification(c.conversationId!)
          void c.reload()
          return res.resend
        } catch (e) {
          throw new Error(e instanceof ApiError ? e.message_he : 'לא הצלחנו לשלוח קישור חדש. נסו שוב בעוד רגע.')
        }
      }
    : undefined
  const tail = (
    <div className="answer-tail" ref={tailRef}>
      {area}
      {carActions}
      {inquiry && (
        <RepInquiryCard
          key={`${inquiry.opened}:${inquiry.intent}:${inquiry.optionId ?? ''}`}
          conversationId={c.conversationId ?? ''}
          initialQuestion={inquiry.question}
          intent={inquiry.intent}
          optionId={inquiry.optionId}
          carLabel={inquiry.carLabel}
          catalogAmount={inquiry.catalogAmount}
          subject={inquiry.subject}
          knownEmail={repEmail}
          emailEnabled={c.emailEnabled}
          emailSimulated={c.emailSimulated}
          disabled={busy}
          onCancel={() => setInquiry(null)}
          onSaved={(res) => { onInquirySaved(res); }}
        />
      )}
    </div>
  )

  if (c.returnTokenPending) {
    return (
      <EmailReturnCard error={c.returnTokenError} invalid={c.returnLinkInvalid} canReturn={c.returnStored}
                       sending={c.sending} onOpen={() => void c.resumeEmailLink()}
                       onNew={() => void c.newConversation()} />
    )
  }

  return (
    <div className={`chat is-chat${c.waitingForRep ? ' is-waiting' : ''}`}>
      <FlowCar mode="band" />
      <header className="chat-header">
        <div className="chat-title">
          <h1>יועץ הרכב</h1>
          {(() => {
            const badge = engineBadge(c.engine)
            return (
              <span className={`status status-${badge.tone}`} title={badge.title} data-engine={c.engine ?? ''}
                    aria-live="polite">
                <i className="status-dot" />{badge.text}
              </span>
            )
          })()}
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
                       onResendVerification={resendVerification}
                       mapCard={c.optionMap && <OptionMapSummary map={c.optionMap} onOpen={() => setMapOpen(true)} />}
                       tail={c.loading ? null : tail}
                       onRetry={(seq) => void c.retry(seq)} onCorrect={firstVisit ? undefined : correct} busy={busy}
                       repCopayOffer={repCopayOffer} onSend={c.send} todayCopay={c.todayCopay}
                       todayCopaySource={c.todayCopaySource} focusQuestionId={c.focusQuestionId}
                       todayCard={todayCard} mapAnchor={c.mapAnchor} />
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
        <ComparisonPanel results={mapCompare} stale={c.optionMap.stale} expects={c.expects} disabled={busy} browse
                         onClose={() => setMapCompare(null)} onSend={c.send} details={catalog}
                         onMoved={() => { setMapCompare(null); setMapOpen(false) }}
                         onCorrect={() => { setMapCompare(null); setMapOpen(false); correct() }} />
      )}
    </div>
  )
}
