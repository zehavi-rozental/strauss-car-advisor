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
  }, [])

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
  }, [applyView, sync])

  /** Sends one message. Returns false if it failed (the caller restores the typed text). */
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
    sending, loading, error, send, dismissError: () => setError(null),
  }
}
