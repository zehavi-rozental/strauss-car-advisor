import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { OptionResult } from '../api/types'
import { formatTime } from '../format/time'
import { BrandMark } from '../ui/BrandMark'
import { Icon } from '../ui/Icon'
import { ResultCard } from './ResultCard'
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

// The compact "חסר עוד" row replaces this server line, so the bubble does not repeat it
const withoutLeftoverTail = (text: string) =>
  text.split('\n').filter((l) => !l.startsWith('חסר עוד:')).join('\n').trim()

const plain = (text: string) => text.replace(/\*\*/g, '')

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // older browsers / no permission: the classic way
    const area = document.createElement('textarea')
    area.value = text
    area.setAttribute('readonly', '')
    area.style.position = 'fixed'
    area.style.opacity = '0'
    document.body.appendChild(area)
    area.select()
    const ok = document.execCommand('copy')
    area.remove()
    return ok
  }
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

  const copy = async (m: ChatMessage, text: string) => {
    if (await copyText(text)) setCopied(m.seq)
  }

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
        const text = bot ? withoutLeftoverTail(m.content) : m.content
        const done = copied === m.seq
        const copyButton = (
          <button type="button" className="msg-action" onClick={() => copy(m, plain(text))}
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
                      {onCorrect && !m.failed && /\d/.test(m.content) && (
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
