// The server's readiness in a quiet line under the header (shell/backendReady.ts): nothing while checking or
// ready, a short notice while it starts, and a friendly message with "לנסות שוב" when it does not answer.
import { Icon } from '../ui/Icon'
import type { Readiness } from './backendReady'

const TEXT: Record<'waking' | 'unavailable' | 'busy', string> = {
  waking: 'מכינים את יועץ הרכב. אפשר בינתיים להכיר את המסך.',
  unavailable: 'השירות אינו זמין כרגע. אפשר לנסות שוב בעוד כמה דקות.',
  busy: 'יש כרגע עומס על השירות. אפשר לנסות שוב מאוחר יותר.',
}

export function ServiceStatus({ state, onRetry }: { state: Readiness; onRetry: () => void }) {
  if (state === 'checking' || state === 'ready') return null
  const waking = state === 'waking'
  return (
    <div className={`service-status${waking ? '' : ' is-down'}`} role="status" aria-live="polite" data-state={state}>
      {waking ? <span className="loading-bar" aria-hidden="true" /> : <Icon name="alert" size={18} />}
      <span>{TEXT[state]}</span>
      {!waking && (
        <button type="button" className="service-retry" onClick={onRetry}>
          <Icon name="retry" size={16} />לנסות שוב
        </button>
      )}
    </div>
  )
}
