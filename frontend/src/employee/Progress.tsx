// A small progress indicator in the header: פרטים · רכב היום · חלופות · תוצאה, the current step highlighted.
import type { Stage } from '../api/types'
import { PROGRESS_STEPS, progressStep } from './widgets'

export function Progress({ stage, hasResults }: { stage: Stage | null; hasResults: boolean }) {
  const current = progressStep(stage, hasResults)
  return (
    <ol className="progress" aria-label="התקדמות">
      {PROGRESS_STEPS.map((label, i) => (
        <li key={label} className={i === current ? 'is-current' : i < current ? 'is-done' : undefined}
            aria-current={i === current ? 'step' : undefined}>
          {label}
        </li>
      ))}
    </ol>
  )
}
