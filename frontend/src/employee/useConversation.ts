// The employee screen's state. Everything shown comes from the server: messages, stage, expects,
// results and open rep questions. The client only orders and displays them.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, api } from '../api/client'
import { normalizeResults } from '../api/normalize'
import type { ConversationView, Expects, Message, MessageIn, OptionResult, PendingQuestion, Stage } from '../api/types'

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

function lastAssistantSeq(messages: Message[]): number | null {
  for (let i = messages.length - 1; i >= 0; i--) if (messages[i].role === 'assistant') return messages[i].seq
  return null
}

export function useConversation() {
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [stage, setStage] = useState<Stage | null>(null)
  const [expects, setExpects] = useState<Expects | null>(null)
  const [results, setResults] = useState<OptionResult[]>([])
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
  const failedRef = useRef(new Map<number, Failed>())

  const applyView = useCallback((view: ConversationView) => {
    const res = normalizeResults(view.results)
    idRef.current = view.conversation_id
    setConversationId(view.conversation_id)
    setMessages(view.messages)
    lastSeqRef.current = view.messages.reduce((m, x) => Math.max(m, x.seq), 0)
    lastAssistantRef.current = lastAssistantSeq(view.messages)
    setStage(view.stage)
    setExpects(view.expects)
    setResults(res)
    runKeyRef.current = runKey(res)
    setResultsAnchor(res.length ? lastAssistantRef.current : null)
  }, [])

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
      setExpects(u.expects)
      setPendingQuestions(u.pending_questions)
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
  }, [])

  const reload = useCallback(async () => {
    const id = idRef.current
    if (!id) return
    failedRef.current.clear()
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
  }, [applyView, sync])

  /** Sends one message. Returns false if it failed and nothing was kept (the caller restores the typed text); a typed
   *  message lost on the way stays on screen as "not sent" with "לשלוח שוב", and counts as handled. */
  const send = useCallback(async (body: MessageIn, display: string): Promise<boolean> => {
    const id = idRef.current
    if (!id || sending) return false
    setError(null)
    setSending(true)
    setMessages((prev) => [...prev, { seq: -Date.now(), role: 'user', source: 'user', content: display,
                                      created_at: new Date().toISOString(), pending: true }])
    try {
      const turn = await api.sendMessage(id, body)
      setStage(turn.stage)
      setExpects(turn.expects)
      await sync()
      return true
    } catch (e) {
      if (e instanceof ApiError && RETRYABLE(e.status) && 'text' in body) {
        // keep the bubble, marked as not sent, with "לשלוח שוב"
        const seq = -Date.now()
        failedRef.current.set(seq, { body, display })
        setMessages((prev) => [...prev.filter((m) => !m.pending),
          { seq, role: 'user', source: 'user', content: display, created_at: new Date().toISOString(), failed: true }])
        setError(e.message_he)
        return true
      }
      setMessages((prev) => prev.filter((m) => !m.pending))
      if (e instanceof ApiError) {
        setError(e.message_he)
        if (e.status === 409) await reload().catch(() => undefined)
      } else {
        setError(String(e))
      }
      return false
    } finally {
      setSending(false)
    }
  }, [sending, sync, reload])

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
  }, [sending, sync, reload])

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
  }, [sync])

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

  return {
    messages, stage, expects, results, resultsAnchor, pendingQuestions, waitingForRep,
    sending, loading, error, send, retry, loadProfile, newConversation, dismissError: () => setError(null),
  }
}
