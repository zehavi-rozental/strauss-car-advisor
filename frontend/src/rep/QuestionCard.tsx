import type { RepQuestion } from '../api/types'
import { inquiryNumber } from '../format/inquiry'
import { formatTime } from '../format/time'
import { Icon } from '../ui/Icon'
import { AnswerForm } from './AnswerForm'
import { CONTEXT_LABELS, MODE_LABELS, questionTitle } from './labels'
import { relativeTime, waitingDuration } from './repForm'

interface Props {
  question: RepQuestion
  token: string
  onAnswered: (answered: RepQuestion) => void
  onUnauthorized: () => void
  onConflict: () => void
}

const REASON_ICON: Record<string, string> = {
  NEW_CAR_COPAY: 'swap', CURRENT_COPAY: 'road', CURRENT_USE_VALUE: 'doc', OCCUPATION_OPT_OUT: 'user',
  MAINTENANCE_AMOUNT: 'form', GENERAL_QUESTION: 'info', ELIGIBILITY_GROUP: 'user',
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
      {/* real server timestamps; the rep never sets them. Open: when it arrived + how long it has waited. */}
      <p className="rep-times">
        <span>פנייה <bdi dir="ltr">{inquiryNumber(q.id)}</bdi></span>
        <span>נפתחה: {formatTime(q.created_at)}</span>
        {open ? (
          <span className="rep-waiting">{waitingDuration(q.created_at)}</span>
        ) : q.answered_at ? (
          <span>נענתה: {formatTime(q.answered_at)}</span>
        ) : null}
      </p>
      <p className="rep-question">{q.question_text}</p>
      <dl className="rep-context">
        {context.map(([label, v]) => <div key={label}><dt>{label}</dt><dd>{v}</dd></div>)}
        <div><dt>סוג</dt><dd>{MODE_LABELS[q.mode] ?? q.mode}</dd></div>
      </dl>
      {open ? (
        <AnswerForm {...props} />
      ) : (
        // the status line (who, when), then the answer itself in its own block, full width: a multi-line answer
        // is never squeezed into a narrow column beside the status (QA 29/10, 375px)
        <div className="answered">
          <p className="answered-line">
            <Icon name="check" size={16} />
            נענתה{q.answered_by ? ` · ${q.answered_by}` : ''}
            {q.answered_at ? ` · ${formatTime(q.answered_at)}` : ''}
            {q.effective_date ? ` · חלה מ-${q.effective_date.split('-').reverse().join('.')}` : ''}
          </p>
          <p className="answered-value"><bdi>{q.answer_value}</bdi></p>
          {q.answer_note ? <p className="answered-note">הערה לעובד: <bdi>{q.answer_note}</bdi></p> : null}
        </div>
      )}
    </article>
  )
}
