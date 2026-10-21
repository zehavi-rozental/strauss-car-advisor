import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { OptionResult } from '../api/types'
import { formatTime } from '../format/time'
import { BrandMark } from '../ui/BrandMark'
import { Icon } from '../ui/Icon'
import { ResultCard } from './ResultCard'
import { bubbleText, copyText, copyableText, lastUserSeq as findLastUserSeq } from './messages'
import { RichText } from './richText'
import { SummaryCard } from './SummaryCard'
import type { ChatMessage } from './useConversation'

interface Props {
  messages: ChatMessage[]
  results: OptionResult[]
  resultsAnchor: number | null
  typing: boolean
  onOpenTable?: () => void
  /** the current question's widget, drawn under the latest message */
  tail?: ReactNode
  /** "לשלוח שוב" for a typed message that was lost on the way */
  onRetry?: (seq: number) => void
  /** "תיקון נתונים" from a message of the employee: the form that saves through {fields} and recalculates */
  onCorrect?: () => void
  busy?: boolean
}

export function MessageList({ messages, results, resultsAnchor, typing, onOpenTable, tail, onRetry, onCorrect, busy }: Props) {
  const end = useRef<HTMLDivElement>(null)
  const [copied, setCopied] = useState<number | null>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, results, typing, tail != null])
  useEffect(() => {
    if (copied === null) return
    const t = window.setTimeout(() => setCopied(null), 1600)
    return () => window.clearTimeout(t)
  }, [copied])

  const copy = async (m: ChatMessage) => {
    if (await copyText(copyableText(m))) setCopied(m.seq)
  }

  // "תיקון נתונים" sits on the employee's latest message only; it opens the correction form, which the server saves
  // and recalculates (the text of a sent message never changes)
  const lastUserSeq = findLastUserSeq(messages)
  const anchored = resultsAnchor != null && messages.some((m) => m.seq === resultsAnchor)
  const cards = results.length > 0 && (
    <div className="result-cards">
      {results.length > 1
        ? <SummaryCard items={results} onOpenTable={onOpenTable} />
        : results.map((r) => <ResultCard key={`${r.option_id}:${r.run_id}`} item={r} onOpenTable={onOpenTable} />)}
    </div>
  )

  return (
    <div className="messages" role="log" aria-live="polite">
      {messages.map((m) => {
        const bot = m.role === 'assistant'
        const text = bubbleText(m)
        const done = copied === m.seq
        const copyButton = (
          <button type="button" className="msg-action" onClick={() => copy(m)}
                  aria-label={done ? 'הועתק' : 'העתקה'} title={done ? 'הועתק' : 'העתקה'}>
            <Icon name={done ? 'check' : 'copy'} size={16} />
          </button>
        )
        return (
          <div key={m.seq} className={`message-group ${bot ? 'from-bot' : 'from-user'}`}>
            {bot && m.source === 'rep' && (
              <div className="event-pill"><Icon name="rep" size={16} />הנציג ענה · {formatTime(m.created_at)}</div>
            )}
            {bot ? (
              <div className="bubble bubble-bot">
                <span className="bubble-avatar" aria-hidden="true"><BrandMark size={22} /></span>
                <div className="bubble-main">
                  <div className="bubble-text"><RichText text={text} /></div>
                  <div className="bubble-meta">
                    <span className="bubble-time">{formatTime(m.created_at)}</span>
                    <span className="msg-actions">{copyButton}</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className={`bubble bubble-user${m.failed ? ' is-failed' : ''}`} aria-busy={m.pending || undefined}>
                <div className="bubble-text"><RichText text={text} /></div>
                <div className="bubble-meta">
                  {m.failed ? (
                    <>
                      <span className="bubble-failed"><Icon name="alert" size={14} />לא נשלחה</span>
                      <button type="button" className="btn btn-sm btn-quiet bubble-retry" disabled={busy}
                              onClick={() => onRetry?.(m.seq)}>
                        <Icon name="retry" size={16} />לשלוח שוב
                      </button>
                    </>
                  ) : (
                    <span className="bubble-time">{m.pending ? 'נשלח…' : formatTime(m.created_at)}</span>
                  )}
                  {!m.pending && (
                    <span className="msg-actions">
                      {copyButton}
                      {onCorrect && m.seq === lastUserSeq && (
                        <button type="button" className="msg-action" onClick={onCorrect} disabled={busy}
                                aria-label="תיקון נתונים" title="תיקון נתונים: הערכים נשמרים מחדש והנטו מחושב מחדש">
                          <Icon name="edit" size={16} />
                        </button>
                      )}
                    </span>
                  )}
                </div>
              </div>
            )}
            {anchored && m.seq === resultsAnchor && cards}
          </div>
        )
      })}
      {!anchored && cards}
      {typing && (
        <div className="typing" aria-label="מקליד">
          <BrandMark size={24} thinking />
          <span>מקליד</span><span className="dots" aria-hidden="true"><i /><i /><i /></span>
        </div>
      )}
      {tail}
      <span className="visually-hidden" aria-live="polite">{copied !== null ? 'הועתק' : ''}</span>
      <div ref={end} />
    </div>
  )
}
