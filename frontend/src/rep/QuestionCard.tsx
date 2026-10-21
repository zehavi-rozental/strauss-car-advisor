import type { RepQuestion } from '../api/types'
import { formatTime } from '../format/time'
import { AnswerForm } from './AnswerForm'
import { CONTEXT_LABELS, MODE_LABELS, questionTitle } from './labels'
import { relativeTime } from './repForm'

interface Props {
  question: RepQuestion
  token: string
  onAnswered: () => void
  onUnauthorized: () => void
  onConflict: () => void
}

export function QuestionCard(props: Props) {
  const q = props.question
  const open = q.status === 'OPEN'
  const context = Object.entries(CONTEXT_LABELS)
    .map(([key, label]) => [label, q.context[key]] as const)
    .filter(([, v]) => v)
  return (
    <article className="rep-card">
      <header>
        <h3>{questionTitle(q.missing_field)}</h3>
        <span className="time-pill">{relativeTime(open ? q.created_at : q.answered_at ?? q.created_at)}</span>
      </header>
      <p className="rep-question">{q.question_text}</p>
      <dl className="rep-context">
        {context.map(([label, v]) => <div key={label}><dt>{label}</dt><dd>{v}</dd></div>)}
        <div><dt>סוג</dt><dd>{MODE_LABELS[q.mode] ?? q.mode}</dd></div>
      </dl>
      {open ? (
        <AnswerForm {...props} />
      ) : (
        <p className="answered-line">
          נענתה: <bdi>{q.answer_value}</bdi>{q.answered_by ? ` · ${q.answered_by}` : ''}
          {q.answered_at ? ` · ${formatTime(q.answered_at)}` : ''}
          {q.answer_note ? <span className="answered-note">הערה לעובד: <bdi>{q.answer_note}</bdi></span> : null}
        </p>
      )}
    </article>
  )
}
