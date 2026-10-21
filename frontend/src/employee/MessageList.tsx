import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import type { OptionResult } from '../api/types'
import { formatTime } from '../format/time'
import { ResultCard } from './ResultCard'
import { RichText } from './richText'
import { SummaryCard } from './SummaryCard'
import type { ChatMessage } from './useConversation'

interface Props {
  messages: ChatMessage[]
  results: OptionResult[]
  /** the results predate the last change: kept as history, marked, never a current recommendation */
  resultsStale?: boolean
  resultsAnchor: number | null
  typing: boolean
  onOpenTable?: () => void
  /** the current question's widget, drawn under the latest message */
  tail?: ReactNode
}

// The compact "חסר עוד" row replaces this server line, so the bubble does not repeat it
const withoutLeftoverTail = (text: string) =>
  text.split('\n').filter((l) => !l.startsWith('חסר עוד:')).join('\n').trim()

export const STALE_NOTE = 'לא מעודכן: החישוב הזה נעשה לפני השינוי האחרון, ויחושב מחדש כשכל הפרטים יהיו ידועים.'

export function MessageList({ messages, results, resultsStale = false, resultsAnchor, typing, onOpenTable, tail }: Props) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, results, typing, tail != null])

  const anchored = resultsAnchor != null && messages.some((m) => m.seq === resultsAnchor)
  const cards = results.length > 0 && (
    <div className={resultsStale ? 'result-cards result-stale' : 'result-cards'} data-stale={resultsStale || undefined}>
      {resultsStale && <p className="stale-note" role="status">{STALE_NOTE}</p>}
      {results.length > 1
        ? <SummaryCard items={results} ranked={!resultsStale} onOpenTable={onOpenTable} />
        : results.map((r) => <ResultCard key={`${r.option_id}:${r.run_id}`} item={r} onOpenTable={onOpenTable} />)}
    </div>
  )

  return (
    <div className="messages" role="log" aria-live="polite">
      {messages.map((m) => (
        <div key={m.seq} className="message-group">
          {m.role === 'assistant' && m.source === 'rep' && (
            <div className="event-pill">הנציג ענה · {formatTime(m.created_at)}</div>
          )}
          <div className={m.role === 'user' ? 'bubble bubble-user' : 'bubble bubble-bot'}
               aria-busy={m.pending || undefined}>
            <div className="bubble-text"><RichText text={m.role === 'assistant' ? withoutLeftoverTail(m.content) : m.content} /></div>
            <div className="bubble-time">{m.pending ? 'נשלח…' : formatTime(m.created_at)}</div>
          </div>
          {anchored && m.seq === resultsAnchor && cards}
        </div>
      ))}
      {!anchored && cards}
      {typing && (
        <div className="bubble bubble-bot typing" aria-label="מקליד">
          <span>מקליד</span><span className="dots"><i /><i /><i /></span>
        </div>
      )}
      {tail}
      <div ref={end} />
    </div>
  )
}
