// The employee screen's state. Everything shown comes from the server: messages, stage, expects,
// results and open rep questions. The client only orders and displays them.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import { normalizeResults } from '../api/normalize'
import type { ConversationView, EngineState, Expects, Message, MessageIn, OptionMap, OptionResult, PendingQuestion, ProfileFields, RepInquiry, Stage } from '../api/types'

const STORAGE_KEY = 'sca.conversationId'
const POLL_MS = 3000

export interface ChatMessage extends Message {
  pending?: boolean          // optimistic bubble, not yet saved by the server
  failed?: boolean           // a typed message the server did not confirm; it can be sent again ("לשלוח שוב")
}

/** A typed message whose request failed on the way (no connection, a server error, too many requests). */
interface Failed { body: MessageIn; display: string }

// worth sending again: the request may never have arrived. A 422 / 404 / 409 is the server's answer, not a loss.
const RETRYABLE = (status: number) => status === 0 || status === 429 || status >= 500

/** An email link that failed: worth trying again with the same token only when the request may not have reached
 *  the server (no connection, too many requests, a server error). A 410 (used or expired) and any other answer
 *  would refuse the same token again. */
export const returnLinkRetryable = (e: unknown) => !(e instanceof ApiError) || RETRYABLE(e.status)

function readStoredId(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

function storeId(id: string): void {
  try {
    localStorage.setItem(STORAGE_KEY, id)
  } catch {
    // private mode: the conversation just won't survive a refresh
  }
}

/** What makes the result cards change: a new calculation (run_id), or the copay's status without one (the
 *  representative approves the catalog amount: same run, now "אושר"; or the employee states an expected amount). */
const runKey = (results: OptionResult[]) => results.map((r) => [
  r.option_id, r.run_id ?? '', r.result.copay_pending, r.result.copay_source ?? '', r.result.copay_effective_date ?? '',
  r.result.employee_stated_copay ?? '',
].join(':')).join('|')

/** The same question as before (polling re-sends it every few seconds): keep the old object, so nothing that
 *  depends on it (an open selection card, a form being filled) is reset by a poll. */
const sameExpects = (a: Expects | null, b: Expects | null) => JSON.stringify(a) === JSON.stringify(b)

export type SendResult = { ok: true } | { ok: false; errors: Record<string, string> }

function lastAssistantSeq(messages: Message[]): number | null {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === 'assistant') return messages[i].seq
  return null
}

export function useConversation() {
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [stage, setStage] = useState<Stage | null>(null)
  const [engine, setEngine] = useState<EngineState | null>(null)   // the badge: /api/health at load, then each turn
  const [repEmail, setRepEmail] = useState<string | null>(null)
  // an older server does not say: mail is assumed on (the card asks for an address, as before)
  const [emailEnabled, setEmailEnabled] = useState(true)
  const [emailSimulated, setEmailSimulated] = useState(false)   // fake mail: say no real email is sent
  const [emailPending, setEmailPending] = useState(false)     // an address waits for its verification
  const [expectsState, setExpectsState] = useState<Expects | null>(null)
  const setExpects = useCallback((next: Expects | null) => {
    setExpectsState((prev) => (sameExpects(prev, next) ? prev : next))
  }, [])
  const [results, setResults] = useState<OptionResult[]>([])
  const [resultsStale, setResultsStale] = useState(false)
  const [todayCopayProvisional, setTodayCopayProvisional] = useState<string | null>(null)
  const [todayCopay, setTodayCopay] = useState<string | null>(null)
  const [todayCopaySource, setTodayCopaySource] = useState<string | null>(null)   // the employee's own amount, not the catalog's
  const [mapReady, setMapReady] = useState(false)          // the server can build the option map now
  const [optionMapState, setOptionMapState] = useState<OptionMap | null>(null)
  const setOptionMap = useCallback((next: OptionMap | null | undefined) => {
    setOptionMapState((prev) => (JSON.stringify(prev) === JSON.stringify(next ?? null) ? prev : next ?? null))
  }, [])
  const [lastAssistant, setLastAssistant] = useState<number | null>(null)
  const [resultsAnchor, setResultsAnchor] = useState<number | null>(null)
  const [pendingQuestions, setPendingQuestions] = useState<PendingQuestion[]>([])
  const [repQuestions, setRepQuestions] = useState<RepInquiry[]>([])
  const [returnTokenPending, setReturnTokenPending] = useState(false)
  // the representative's answer to bring into view (1/11): from the email link, or an update waiting for the
  // employee; cleared by the next message, so the chat follows the conversation again
  const [focusQuestionId, setFocusQuestionId] = useState<string | null>(null)
  const [returnTokenError, setReturnTokenError] = useState<string | null>(null)
  // the link was refused for good: its token is dropped; a conversation stored in this browser may be reopened
  // (the server still checks the session cookie)
  const [returnLinkInvalid, setReturnLinkInvalid] = useState(false)
  const [returnStored, setReturnStored] = useState(false)
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const idRef = useRef<string | null>(null)
  const lastSeqRef = useRef(0)
  const lastAssistantRef = useRef<number | null>(null)
  const runKeyRef = useRef('')
  const syncChain = useRef<Promise<void>>(Promise.resolve())
  const started = useRef(false)
  const emailReturnToken = useRef<string | null>(null)
  const failedRef = useRef(new Map<number, Failed>())

  const applyView = useCallback((view: ConversationView) => {
    const res = normalizeResults(view.results)
    idRef.current = view.conversation_id
    setConversationId(view.conversation_id)
    setMessages(view.messages)
    setRepQuestions(view.rep_questions ?? [])
    lastSeqRef.current = view.messages.reduce((m, x) => Math.max(m, x.seq), 0)
    lastAssistantRef.current = lastAssistantSeq(view.messages)
    setStage(view.stage)
    setRepEmail(view.rep_email ?? null)
    setEmailEnabled(view.rep_email_delivery !== 'disabled')
    setEmailSimulated(view.rep_email_simulated === true)
    setEmailPending(view.rep_email_pending === true)
    setExpects(view.expects)
    const offer = view.expects?.fields?.find((f) => f.field === 'rep_copay')?.choices?.[0]?.id
    if (offer?.startsWith('apply:')) setFocusQuestionId(offer.slice('apply:'.length))
    setLastAssistant(lastAssistantRef.current)
    setResultsStale(Boolean(view.results_stale))
    setTodayCopay(view.today_copay ?? null)
    setTodayCopaySource(view.today_copay_source ?? null)
    setTodayCopayProvisional(view.today_copay_provisional ?? null)
    setMapReady(view.map_ready === true)
    setOptionMap(view.option_map)
    setResults(res)
    runKeyRef.current = runKey(res)
    setResultsAnchor(res.length ? lastAssistantRef.current : null)
  }, [setExpects, setOptionMap])

  /** New messages after the last seq, plus the current stage, expects, results and open questions.
   *  Calls are chained so polling and sending never apply updates out of order. */
  const sync = useCallback((): Promise<Message[]> => {
    const run = async (): Promise<Message[]> => {
      const id = idRef.current
      if (!id) return []
      const u = await api.getUpdates(id, lastSeqRef.current)
      const fresh = u.messages.filter((m) => m.seq > lastSeqRef.current)
      if (fresh.length) lastSeqRef.current = Math.max(...fresh.map((m) => m.seq))
      lastAssistantRef.current = lastAssistantSeq(fresh) ?? lastAssistantRef.current
      setMessages((prev) => [...prev.filter((m) => !m.pending), ...fresh])
      setStage(u.stage)
      if (u.rep_email_verified) setRepEmail(u.rep_email ?? null)
      if (u.rep_email_delivery) setEmailEnabled(u.rep_email_delivery !== 'disabled')
      if (u.rep_email_pending !== undefined) setEmailPending(u.rep_email_pending)
      if (u.rep_email_simulated !== undefined) setEmailSimulated(u.rep_email_simulated)
      setExpects(u.expects)
      setLastAssistant(lastAssistantRef.current)
      setResultsStale(Boolean(u.results_stale))
      if (u.today_copay !== undefined) setTodayCopay(u.today_copay)
      if (u.today_copay_source !== undefined) setTodayCopaySource(u.today_copay_source)
      if (u.today_copay_provisional !== undefined) setTodayCopayProvisional(u.today_copay_provisional)
      setMapReady(u.map_ready === true)
      setOptionMap(u.option_map)
      setPendingQuestions(u.pending_questions)
      setRepQuestions(u.rep_questions ?? [])
      const res = normalizeResults(u.results)
      const key = runKey(res)
      if (key !== runKeyRef.current) {
        runKeyRef.current = key
        setResults(res)
        // the card moves to the message in which the results changed (e.g. the rep's answer)
        setResultsAnchor(res.length ? lastAssistantRef.current : null)
      }
      return fresh
    }
    const next = syncChain.current.then(run, run)
    syncChain.current = next.then(() => undefined, () => undefined)
    return next
  }, [setExpects, setOptionMap])

  const reload = useCallback(async () => {
    const id = idRef.current
    if (!id) return
    failedRef.current.clear()
    applyView(await api.getConversation(id))
    await sync()
  }, [applyView, sync])

  // the engine's real state at load (no model call); each turn then says what answered it
  useEffect(() => {
    api.health().then((h) => { if (h.engine) setEngine((e) => e ?? h.engine ?? null) }).catch(() => {})
  }, [])

  // load: the stored conversation, or a new one
  useEffect(() => {
    if (started.current) return
    started.current = true
    ;(async () => {
      try {
        let stored = readStoredId()
        const returnToken = new URLSearchParams(window.location.hash.slice(1)).get('return_token')
        if (returnToken) {
          emailReturnToken.current = returnToken
          const url = new URL(window.location.href)
          url.hash = ''
          window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
          setReturnTokenPending(true)
          setLoading(false)
          return
        }
        let view: ConversationView | null = null
        if (stored) {
          try {
            view = await api.getConversation(stored)
          } catch (e) {
            if (!(e instanceof ApiError && e.status === 404)) throw e
          }
        }
        if (view) {
          applyView(view)
        } else {
          const turn = await api.createConversation()
          storeId(turn.conversation_id)
          idRef.current = turn.conversation_id
          setConversationId(turn.conversation_id)
          setStage(turn.stage)
          setRepEmail(null)
          setExpects(turn.expects)
        }
        await sync()
        setError(null)
      } catch (e) {
        setError(e instanceof ApiError ? e.message_he : String(e))
      } finally {
        setLoading(false)
      }
    })()
  }, [applyView, sync, setExpects])

  const resumeEmailLink = useCallback(async () => {
    const token = emailReturnToken.current
    if ((!token && !idRef.current) || sending) return
    setSending(true)
    setReturnTokenError(null)
    try {
      let targetQuestionId: string | null = null
      if (token) {
        const returned = await api.redeemEmailLink(token)
        emailReturnToken.current = null
        storeId(returned.conversation_id)
        idRef.current = returned.conversation_id
        targetQuestionId = returned.question_id
      }
      const id = idRef.current
      if (!id) return
      const view = await api.getConversation(id)
      applyView(view)
      await sync()
      setReturnTokenPending(false)
      // the answer the email is about: the list keeps it in view (a one-off scroll was overridden by the list's
      // own scroll to the bottom), until the employee sends the next message
      if (targetQuestionId) setFocusQuestionId(targetQuestionId)
    } catch (e) {
      if (!returnLinkRetryable(e)) {
        if (token) {
          emailReturnToken.current = null
          setReturnLinkInvalid(true)
        }
        // after a refused link: the conversation stored here, if any; a stored one the server refused: none
        const stored = token ? readStoredId() : null
        idRef.current = stored
        setReturnStored(stored !== null)
      }
      setReturnTokenError(e instanceof ApiError ? e.message_he : String(e))
    } finally {
      setSending(false)
    }
  }, [applyView, sending, sync])

  /** Sends one message. The result says whether it was handled, and (for the profile form) which fields the server
   *  refused, so the form can show each error next to its field and keep everything typed. A typed message lost on
   *  the way stays on screen as "not sent" with "לשלוח שוב", and counts as handled. */
  const deliver = useCallback(async (body: MessageIn, display: string): Promise<SendResult> => {
    setFocusQuestionId(null)
    const id = idRef.current
    if (!id || sending) return { ok: false, errors: {} }
    setError(null)
    setSending(true)
    setMessages((prev) => [...prev, { seq: -Date.now(), role: 'user', source: 'user', content: display,
                                      created_at: new Date().toISOString(), pending: true }])
    try {
      const turn = await api.sendMessage(id, body)
      if (turn.engine) setEngine(turn.engine)
      setStage(turn.stage)
      setExpects(turn.expects)
      await sync()
      return { ok: true }
    } catch (e) {
      if (e instanceof ApiError && RETRYABLE(e.status) && 'text' in body) {
        // keep the bubble, marked as not sent, with "לשלוח שוב"
        const seq = -Date.now()
        failedRef.current.set(seq, { body, display })
        setMessages((prev) => [...prev.filter((m) => !m.pending),
          { seq, role: 'user', source: 'user', content: display, created_at: new Date().toISOString(), failed: true }])
        setError(e.message_he)
        return { ok: true }                    // handled: kept on screen with "לשלוח שוב"
      }
      setMessages((prev) => prev.filter((m) => !m.pending))
      if (e instanceof ApiError) {
        setError(e.message_he)
        if (e.status === 409) await reload().catch(() => undefined)
        return { ok: false, errors: e.errors }
      }
      setError(String(e))
      return { ok: false, errors: {} }
    } finally {
      setSending(false)
    }
  }, [sending, sync, reload, setExpects])

  /** Sends one message. Returns false if it failed (the caller restores the typed text). */
  const send = useCallback(async (body: MessageIn, display: string): Promise<boolean> =>
    (await deliver(body, display)).ok, [deliver])

  /** The profile form or a reviewed payslip: all or nothing on the server; field errors come back. */
  const sendFields = useCallback((fields: ProfileFields, display: string, source?: 'payslip') =>
    deliver(source ? { fields, source } : { fields }, display), [deliver])

  /** "לשלוח שוב" for a message that failed on the way. First asks the server what it has: if the message did
   *  arrive (only the answer was lost), nothing is sent twice. */
  const retry = useCallback(async (seq: number): Promise<void> => {
    const id = idRef.current
    const f = failedRef.current.get(seq)
    if (!id || !f || sending) return
    setError(null)
    setSending(true)
    const drop = () => {
      failedRef.current.delete(seq)
      setMessages((prev) => prev.filter((m) => m.seq !== seq))
    }
    try {
      const fresh = await sync()
      if (fresh.some((m) => m.role === 'user' && m.content.trim() === f.display.trim())) {
        drop()
        return
      }
      const turn = await api.sendMessage(id, f.body)
      if (turn.engine) setEngine(turn.engine)
      setStage(turn.stage)
      setExpects(turn.expects)
      drop()
      await sync()
    } catch (e) {
      setError(e instanceof ApiError ? e.message_he : String(e))
      if (e instanceof ApiError && e.status === 409) await reload().catch(() => undefined)
    } finally {
      setSending(false)
    }
  }, [sending, sync, reload, setExpects])

  /** The profile as the server holds it, for prefilling the form (the updates feed does not carry it). */
  const loadProfile = useCallback(async (): Promise<Record<string, unknown>> => {
    const id = idRef.current
    return id ? (await api.getConversation(id)).profile : {}
  }, [])

  /** "שיחה חדשה": forget the stored id and open a new conversation (the server replaces the cookie). */
  const newConversation = useCallback(async () => {
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // private mode: nothing stored
    }
    setError(null)
    setLoading(true)
    failedRef.current.clear()
    try {
      const turn = await api.createConversation()
      storeId(turn.conversation_id)
      idRef.current = turn.conversation_id
      lastSeqRef.current = 0
      lastAssistantRef.current = null
      runKeyRef.current = ''
      setConversationId(turn.conversation_id)
      setMessages([])
      setLastAssistant(null)
      setResultsStale(false)
      setOptionMap(null)
      setResults([])
      setResultsAnchor(null)
      setPendingQuestions([])
      setRepQuestions([])
      setStage(turn.stage)
      setExpects(turn.expects)
      emailReturnToken.current = null
      setReturnTokenPending(false)
      setReturnLinkInvalid(false)
      setReturnTokenError(null)
      await sync()
    } catch (e) {
      setError(e instanceof ApiError ? e.message_he : String(e))
    } finally {
      setLoading(false)
    }
  }, [sync, setExpects, setOptionMap])

  // polling: only while a rep question is open and the tab is visible
  const waitingForRep = pendingQuestions.length > 0 || stage === 'WAITING_FOR_REP'
  useEffect(() => {
    if (!waitingForRep || !conversationId) return
    let timer: number | undefined
    const tick = () => { sync().catch(() => undefined) }
    const start = () => {
      if (timer === undefined && document.visibilityState === 'visible') timer = window.setInterval(tick, POLL_MS)
    }
    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        tick()
        start()
      } else {
        stop()
      }
    }
    start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [waitingForRep, conversationId, sync])

  // the question on screen: a new bot message or a different expected answer. A poll that brings neither
  // is the same question, and whatever its link opened stays open.
  const expects = expectsState
  const questionKey = `${lastAssistant ?? ''}|${JSON.stringify(expects)}`

  return {
    engine, focusQuestionId, todayCopay, todayCopaySource, todayCopayProvisional,
    conversationId, repEmail, emailEnabled, emailSimulated, emailPending,
    messages, stage, expects, questionKey, results, resultsStale, mapReady, optionMap: optionMapState, resultsAnchor,
    pendingQuestions,
    repQuestions, returnTokenPending, returnTokenError, returnLinkInvalid, returnStored, resumeEmailLink,
    waitingForRep, sending, loading, error, send, sendFields, retry, reload, loadProfile, newConversation,
    dismissError: () => setError(null),
  }
}
