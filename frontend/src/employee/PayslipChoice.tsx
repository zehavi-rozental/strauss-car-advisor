// The two ways to give the salary details (19/11): answering in the chat, or uploading a payslip and checking what
// was read. Drawn only for the server's payslip_upload action (expects.actions), so it is never offered when the
// server cannot read a payslip. Expanded once, while no salary value is known yet and the employee has not chosen the
// chat; after that a compact button stays, so the conversation never fills with repeated choices.
import type { ExpectsAction } from '../api/types'
import { Icon } from '../ui/Icon'

export const CHOICE_TITLE = 'איך נוח לכם למסור את פרטי השכר?'
export const CHOICE_TEXT = 'את פרטי השכר אפשר למלא כאן בשיחה, שאלה אחר שאלה. מעדיפים לחסוך הקלדה? ' +
  'אפשר גם להעלות תלוש שכר, לבדוק את הנתונים שזוהו ולאשר אותם.'
export const CHAT_LABEL = "להמשיך בהזנה בצ'אט"

interface Props {
  action: ExpectsAction
  expanded: boolean
  disabled: boolean
  onUpload: () => void
  onChat: () => void
}

export function PayslipChoice({ action, expanded, disabled, onUpload, onChat }: Props) {
  if (!expanded) {
    return (
      <button type="button" className="link-button link-payslip_upload persistent-action" disabled={disabled}
              onClick={onUpload}>
        <Icon name="upload" size={18} />{action.label}
      </button>
    )
  }
  return (
    <section className="payslip-choice" aria-label="שתי דרכים למסור את פרטי השכר">
      <p className="payslip-choice-title">{CHOICE_TITLE}</p>
      <p className="payslip-choice-text">{CHOICE_TEXT}</p>
      <div className="payslip-choice-actions">
        <button type="button" className="chip chip-quiet" disabled={disabled} onClick={onChat}>
          <Icon name="edit" size={16} />{CHAT_LABEL}
        </button>
        <button type="button" className="chip link-payslip_upload persistent-action" disabled={disabled}
                onClick={onUpload}>
          <Icon name="upload" size={16} />{action.label}
        </button>
      </div>
    </section>
  )
}
