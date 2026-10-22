import type { RepQuestion } from '../api/types'
import { formatTime } from '../format/time'
import { Icon } from '../ui/Icon'
import { AnswerForm } from './AnswerForm'
import { CONTEXT_LABELS, MODE_LABELS, questionTitle } from './labels'
import { relativeTime } from './repForm'

interface Props {
  question: RepQuestion
  token: string
  onAnswered: (answered: RepQuestion) => void
  onUnauthorized: () => void
  onConflict: () => void
}

const REASON_ICON: Record<string, string> = {
  NEW_CAR_COPAY: 'swap', CURRENT_COPAY: 'road', CURRENT_USE_VALUE: 'doc', OCCUPATION_OPT_OUT: 'user',
  MAINTENANCE_AMOUNT: 'form', GENERAL_QUESTION: 'info',
}

export function QuestionCard(props: Props) {
  const q = props.question
  const open = q.status === 'OPEN'
  const context = Object.entries(CONTEXT_LABELS)
    .map(([key, label]) => [label, q.context[key]] as const)
    .filter(([, v]) => v)
  return (
    <article className={`rep-card ${open ? 'is-open' : 'is-answered'} mode-${q.mode}`}>
      <header>
        <span className="rep-card-icon" aria-hidden="true"><Icon name={REASON_ICON[q.missing_field] ?? 'info'} size={20} /></span>
        <h3>{questionTitle(q.missing_field)}</h3>
        <span className={`mode-pill mode-pill-${q.mode}`}>{MODE_LABELS[q.mode] ?? q.mode}</span>
        <span className="time-pill"><Icon name="clock" size={14} />{relativeTime(open ? q.created_at : q.answered_at ?? q.created_at)}</span>
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
          <Icon name="check" size={16} />
          נענתה: <bdi>{q.answer_value}</bdi>{q.answered_by ? ` · ${q.answered_by}` : ''}
          {q.answered_at ? ` · ${formatTime(q.answered_at)}` : ''}
          {q.answer_note ? <span className="answered-note">הערה לעובד: <bdi>{q.answer_note}</bdi></span> : null}
        </p>
      )}
    </article>
  )
}
