import { useEffect, useRef } from 'react'
import type { OptionResult } from '../api/types'
import { formatTime } from '../format/time'
import { ResultCard } from './ResultCard'
import type { ChatMessage } from './useConversation'

interface Props {
  messages: ChatMessage[]
  results: OptionResult[]
  resultsAnchor: number | null
  typing: boolean
}

export function MessageList({ messages, results, resultsAnchor, typing }: Props) {
  const end = useRef<HTMLDivElement>(null)
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages.length, results, typing])

  const anchored = resultsAnchor != null && messages.some((m) => m.seq === resultsAnchor)
  const cards = results.length > 0 && (
    <div className="result-cards">
      {results.map((r) => <ResultCard key={`${r.option_id}:${r.run_id}`} item={r} />)}
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
            <div className="bubble-text">{m.content}</div>
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
      <div ref={end} />
    </div>
  )
}
