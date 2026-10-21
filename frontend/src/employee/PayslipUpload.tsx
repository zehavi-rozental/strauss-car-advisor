// Payslip upload, extraction, and the editable Hebrew review. Inside the chat:
//   1. pick a PDF/image of the payslip,
//   2. the server reads what the payslip clearly shows (never name / ID / bank) and returns the profile
//      fields it found, missing and unclear ones, plus what it shows for information only,
//   3. the employee keeps, edits or drops each value and explicitly confirms before anything is saved.
// A failure, an unreadable file and a file that is not a payslip each say so, with retry and manual entry.
// The payslip file is never stored by the server; saving happens through the normal {fields} message.
import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, api } from '../api/client'
import type { PayslipInfo, ProfileFields } from '../api/types'
import { formatMoney } from '../format/money'
import { payslipOutcome } from './payslipOutcome'
import type { PayslipOutcome } from './payslipOutcome'
import { CHOOSE_FIRST, assign, assignable, choicesFor, reviewFields, reviewRows } from './payslipReview'
import type { ReviewRow } from './payslipReview'
import type { SendResult } from './useConversation'

interface Props {
  onSubmit: (fields: ProfileFields) => Promise<SendResult>
  onCancel: () => void
  loadProfile: () => Promise<Record<string, unknown>>
  disabled: boolean
}

// MIME types the server accepts (it also re-checks by file signature). Guides the file picker.
const ACCEPT = 'application/pdf,image/jpeg,image/png,image/webp'
const UPLOAD_HINT = 'מעלים צילום או PDF של התלוש. קוראים ממנו רק נתוני שכר והפרשות — לא שם, לא ת"ז ולא פרטי בנק. ' +
  'התלוש עצמו לא נשמר.'
const MANUAL_HINT = 'מלאו את הנתונים ידנית, בחרו מה לעדכן ואשרו. אפשר גם לבטל ולכתוב בצ׳אט.'
const REVIEW_HINT = 'לכל שורה בחרו אם לעדכן את הפרופיל. כשהתלוש שונה ממה שכתבתם בשיחה, בחרו איזה ערך נכון לחישוב.'
const ASSIGN_LABEL: Record<string, string> = { commissions: 'לשייך לעמלות', bonus: 'לשייך לבונוס' }

type Step = 'pick' | 'failed' | 'review'

const infoValue = (i: PayslipInfo) => (i.kind === 'amount' ? formatMoney(Number(i.value)) : i.kind === 'percent'
  ? `${Number(i.value)}%` : String(Number(i.value)))

export function PayslipUpload({ onSubmit, onCancel, loadProfile, disabled }: Props) {
  const [step, setStep] = useState<Step>('pick')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<PayslipOutcome | null>(null)
  const [rows, setRows] = useState<ReviewRow[]>(() => reviewRows([]))
  const [info, setInfo] = useState<PayslipInfo[]>([])
  const [errors, setErrors] = useState<Partial<Record<keyof ProfileFields, string>>>({})
  const inputRef = useRef<HTMLInputElement>(null)

  // What the conversation holds right now, read when the payslip is read (never an earlier copy). If it
  // cannot be read, nothing is preselected: every value with a payslip value waits for the employee.
  const currentProfile = (): Promise<Record<string, unknown> | null> => loadProfile().catch(() => null)

  const pick = () => inputRef.current?.click()

  const onFile = async (file: File | undefined) => {
    if (!file || busy || disabled) return
    setBusy(true)
    setError(null)
    try {
      const [result, profile] = await Promise.all([api.extractPayslip(file), currentProfile()])
      const next = payslipOutcome(result)
      setOutcome(next)
      setErrors({})
      if (next.view === 'failed') {
        setInfo([])
        setStep('failed')
        return
      }
      setRows(reviewRows(result.fields, profile))
      setInfo(result.info ?? [])
      setStep('review')
    } catch (e) {
      setError(e instanceof ApiError ? e.message_he : 'ההעלאה נכשלה. נסו שוב או מלאו ידנית.')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''   // allow re-picking the same file
    }
  }

  // manual entry after a failure: the same review, empty, with no claim that anything was read
  const manual = async () => {
    setOutcome(null)
    setRows(reviewRows([], await currentProfile()))
    setInfo([])
    setErrors({})
    setError(null)
    setStep('review')
  }

  const update = (key: keyof ProfileFields, patch: Partial<ReviewRow>) => {
    setRows((rs) => rs.map((r) => (r.key === key ? { ...r, ...patch } : r)))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  const confirm = async (e: FormEvent) => {
    e.preventDefault()
    const built = reviewFields(rows)
    setErrors(built.errors)
    if (Object.values(built.errors).includes(CHOOSE_FIRST)) {
      setError('יש שורות שבהן צריך לבחור איזה ערך נכון. דבר לא נשמר.')
      return
    }
    if (Object.keys(built.errors).length) return
    if (!Object.keys(built.fields).length) {
      setError('לא נבחר אף שדה לעדכון. בחרו "לעדכן" בשורה אחת לפחות, או בטלו.')
      return
    }
    setError(null)
    setBusy(true)
    const sent = await onSubmit(built.fields)
    setBusy(false)
    if (!sent.ok) {
      // all or nothing: nothing was saved; the review stays as it was, and each refused field says why
      setErrors(sent.errors as Partial<Record<keyof ProfileFields, string>>)
      setError('השמירה נכשלה, ודבר לא נשמר. בדקו את השדות המסומנים ונסו שוב.')
    }
  }

  const working = busy || disabled
  const fileInput = (
    <input ref={inputRef} type="file" accept={ACCEPT} className="visually-hidden" tabIndex={-1}
           aria-hidden="true" onChange={(e) => void onFile(e.target.files?.[0])} />
  )

  if (step === 'pick' || step === 'failed') {
    const failed = step === 'failed' && outcome !== null
    return (
      <div className="profile-form payslip-upload" aria-label="העלאת תלוש">
        <h3>{failed ? outcome.title : 'העלאת תלוש'}</h3>
        {failed
          ? <p className="field-error" role="alert">{outcome.message}</p>
          : <p className="form-hint">{UPLOAD_HINT}</p>}
        {fileInput}
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="form-actions">
          <button type="button" className="chip chip-primary" disabled={working} onClick={pick}>
            {busy ? 'קורא את התלוש…' : !failed ? 'בחירת קובץ' : outcome.retry ? 'לנסות שוב' : 'לנסות קובץ אחר'}
          </button>
          {failed && (
            <button type="button" className="chip chip-quiet" disabled={working} onClick={() => void manual()}>מילוי ידני</button>
          )}
          <button type="button" className="chip chip-quiet" disabled={working} onClick={onCancel}>ביטול</button>
        </div>
      </div>
    )
  }

  const kept = rows.filter((r) => r.choice === 'payslip').length
  return (
    <form className="profile-form payslip-review" onSubmit={confirm} aria-label="סקירת נתוני התלוש">
      <h3>{outcome ? outcome.title : 'מילוי ידני'}</h3>
      <p className="form-hint" role={outcome?.status === 'nothing_found' ? 'status' : undefined}>
        {outcome ? outcome.message : MANUAL_HINT}
      </p>
      {outcome && <p className="form-hint">{REVIEW_HINT}</p>}
      {fileInput}
      <div className="form-grid">
        {rows.map((r) => (
          <div key={r.key} className={`form-field payslip-row payslip-${r.status}`} data-field={r.key}>
            <label htmlFor={`ps-${r.key}`}>
              {r.label}{r.unit && <span className="unit"> ({r.unit})</span>}
            </label>
            <div className="form-input-row">
              <input id={`ps-${r.key}`} inputMode="decimal" autoComplete="off" value={r.value}
                     disabled={working} aria-invalid={errors[r.key] ? true : undefined}
                     onChange={(e) => update(r.key, { value: e.target.value,
                                                      choice: e.target.value.trim() !== '' ? 'payslip' : 'skip' })} />
            </div>
            <div className="review-choice" role="radiogroup" aria-label={`${r.label}: מה לעשות`}>
              {choicesFor(r).map((ch) => (
                <label key={ch.id} className={r.choice === ch.id ? 'choice-option chosen' : 'choice-option'}>
                  <input type="radio" name={`ps-choice-${r.key}`} value={ch.id} checked={r.choice === ch.id}
                         disabled={working} onChange={() => update(r.key, { choice: ch.id })} />
                  <bdi>{ch.label}</bdi>
                </label>
              ))}
            </div>
            {r.source && <span className="field-source">{r.source}</span>}
            {r.note && <span className="field-note" role="note">{r.note}</span>}
            {r.known !== null && (
              <span className={r.differs ? 'field-note' : 'field-source'}>
                {r.differs ? `התלוש שונה ממה שכתבתם בשיחה (${r.known}). בחרו איזה ערך נכון.`
                  : r.same ? 'זהה למה שכתבתם בשיחה' : `בשיחה: ${r.known}`}
              </span>
            )}
            {errors[r.key] && <span className="field-error" role="alert">{errors[r.key]}</span>}
          </div>
        ))}
      </div>
      {info.length > 0 && (
        <section className="payslip-info" aria-label="נמצא בתלוש ולא נכנס לחישוב אוטומטית">
          <h4>נמצא בתלוש ולא נכנס לחישוב אוטומטית</h4>
          <ul>
            {info.map((i, n) => (
              <li key={`${i.key}-${n}`} data-info={i.key}>
                <strong>{i.source_label || i.label}</strong>: <bdi>{infoValue(i)}</bdi>
                <span className="field-source"> {i.note}</span>
                {assignable(i, rows).map((k) => (
                  <button key={k} type="button" className="chip chip-quiet" disabled={working}
                          onClick={() => setRows((rs) => assign(rs, k, i))}>{ASSIGN_LABEL[k]}</button>
                ))}
              </li>
            ))}
          </ul>
        </section>
      )}
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="submit" className="chip chip-primary" disabled={working}>
          {busy ? 'שומר…' : kept ? `אישור ושמירה (${kept})` : 'אישור ושמירה'}
        </button>
        {outcome && outcome.status !== 'found' && (
          <button type="button" className="chip chip-quiet" disabled={working} onClick={pick}>לנסות קובץ אחר</button>
        )}
        <button type="button" className="chip chip-quiet" disabled={working} onClick={onCancel}>ביטול</button>
      </div>
    </form>
  )
}
