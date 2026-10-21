// The route of the consultation: פרטים · רכב היום · חלופות · תוצאה, drawn as a road with four stops.
import type { CSSProperties } from 'react'
import type { Stage } from '../api/types'
import { Icon } from '../ui/Icon'
import { PROGRESS_STEPS, progressStep } from './widgets'

export function Progress({ stage, hasResults }: { stage: Stage | null; hasResults: boolean }) {
  const current = progressStep(stage, hasResults)
  return (
    <ol className="progress" aria-label="התקדמות" style={{ '--step': current } as CSSProperties}>
      {PROGRESS_STEPS.map((label, i) => (
        <li key={label} className={i === current ? 'is-current' : i < current ? 'is-done' : undefined}
            aria-current={i === current ? 'step' : undefined}>
          <span className="progress-dot" aria-hidden="true">{i < current ? <Icon name="check" size={12} /> : null}</span>
          <span className="progress-label">{label}</span>
        </li>
      ))}
    </ol>
  )
}
