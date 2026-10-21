// The employee screen's state. Everything shown comes from the server: messages, stage, expects,
// results and open rep questions. The client only orders and displays them.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import { normalizeResults } from '../api/normalize'
import type { ConversationView, Expects, Message, MessageIn, OptionResult, PendingQuestion, ProfileFields, Stage } from '../api/types'

const STORAGE_KEY = 'sca.conversationId'
const POLL_MS = 3000

export interface ChatMessage extends Message {
  pending?: boolean          // optimistic bubble, not yet saved by the server
}

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

const runKey = (results: OptionResult[]) => results.map((r) => `${r.option_id}:${r.run_id ?? ''}`).join('|')

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
  const [expectsState, setExpectsState] = useState<Expects | null>(null)
  const setExpects = useCallback((next: Expects | null) => {
    setExpectsState((prev) => (sameExpects(prev, next) ? prev : next))
  }, [])
  const [results, setResults] = useState<OptionResult[]>([])
  const [resultsStale, setResultsStale] = useState(false)
  const [lastAssistant, setLastAssistant] = useState<number | null>(null)
  const [resultsAnchor, setResultsAnchor] = useState<number | null>(null)
  const [pendingQuestions, setPendingQuestions] = useState<PendingQuestion[]>([])
  const [sending, setSending] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const idRef = useRef<string | null>(null)
  const lastSeqRef = useRef(0)
  const lastAssistantRef = useRef<number | null>(null)
  const runKeyRef = useRef('')
  const syncChain = useRef<Promise<void>>(Promise.resolve())
  const started = useRef(false)

  const applyView = useCallback((view: ConversationView) => {
    const res = normalizeResults(view.results)
    idRef.current = view.conversation_id
    setConversationId(view.conversation_id)
    setMessages(view.messages)
    lastSeqRef.current = view.messages.reduce((m, x) => Math.max(m, x.seq), 0)
    lastAssistantRef.current = lastAssistantSeq(view.messages)
    setStage(view.stage)
    setExpects(view.expects)
    setLastAssistant(lastAssistantRef.current)
    setResultsStale(Boolean(view.results_stale))
    setResults(res)
    runKeyRef.current = runKey(res)
    setResultsAnchor(res.length ? lastAssistantRef.current : null)
  }, [setExpects])

  /** New messages after the last seq, plus the current stage, expects, results and open questions.
   *  Calls are chained so polling and sending never apply updates out of order. */
  const sync = useCallback((): Promise<void> => {
    const run = async () => {
      const id = idRef.current
      if (!id) return
      const u = await api.getUpdates(id, lastSeqRef.current)
      const fresh = u.messages.filter((m) => m.seq > lastSeqRef.current)
      if (fresh.length) lastSeqRef.current = Math.max(...fresh.map((m) => m.seq))
      lastAssistantRef.current = lastAssistantSeq(fresh) ?? lastAssistantRef.current
      setMessages((prev) => [...prev.filter((m) => !m.pending), ...fresh])
      setStage(u.stage)
      setExpects(u.expects)
      setLastAssistant(lastAssistantRef.current)
      setResultsStale(Boolean(u.results_stale))
      setPendingQuestions(u.pending_questions)
      const res = normalizeResults(u.results)
      const key = runKey(res)
      if (key !== runKeyRef.current) {
        runKeyRef.current = key
        setResults(res)
        // the card moves to the message in which the results changed (e.g. the rep's answer)
        setResultsAnchor(res.length ? lastAssistantRef.current : null)
      }
    }
    const next = syncChain.current.then(run, run)
    syncChain.current = next.catch(() => undefined)
    return next
  }, [setExpects])

  const reload = useCallback(async () => {
    const id = idRef.current
    if (!id) return
    applyView(await api.getConversation(id))
    await sync()
  }, [applyView, sync])

  // load: the stored conversation, or a new one
  useEffect(() => {
    if (started.current) return
    started.current = true
    ;(async () => {
      try {
        const stored = readStoredId()
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

  /** Sends one message. The result says whether it was saved, and (for the profile form) which fields the
   *  server refused, so the form can show each error next to its field and keep everything typed. */
  const deliver = useCallback(async (body: MessageIn, display: string): Promise<SendResult> => {
    const id = idRef.current
    if (!id || sending) return { ok: false, errors: {} }
    setError(null)
    setSending(true)
    setMessages((prev) => [...prev, { seq: -Date.now(), role: 'user', source: 'user', content: display,
                                      created_at: new Date().toISOString(), pending: true }])
    try {
      const turn = await api.sendMessage(id, body)
      setStage(turn.stage)
      setExpects(turn.expects)
      await sync()
      return { ok: true }
    } catch (e) {
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
      setResults([])
      setResultsAnchor(null)
      setPendingQuestions([])
      setStage(turn.stage)
      setExpects(turn.expects)
      await sync()
    } catch (e) {
      setError(e instanceof ApiError ? e.message_he : String(e))
    } finally {
      setLoading(false)
    }
  }, [sync, setExpects])

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
    messages, stage, expects, questionKey, results, resultsStale, resultsAnchor, pendingQuestions, waitingForRep,
    sending, loading, error, send, sendFields, loadProfile, newConversation, dismissError: () => setError(null),
  }
}
