// Payslip upload, extraction, and the editable Hebrew review. Inside the chat:
//   1. pick a PDF/image of the payslip,
//   2. the server reads what the payslip clearly shows (never name / ID / bank) and returns the profile
//      fields it found, missing and unclear ones, plus what it shows for information only,
//   3. the employee keeps, edits or drops each value and explicitly confirms before anything is saved.
// A failure, an unreadable file and a file that is not a payslip each say so, with retry and manual entry.
// The payslip file is never stored by the server; saving happens through the normal {fields} message.
import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, api } from '../api/client'
import type { PayslipInfo, ProfileFields } from '../api/types'
import { formatMoney } from '../format/money'
import { Icon } from '../ui/Icon'
import { payslipOutcome } from './payslipOutcome'
import type { PayslipOutcome } from './payslipOutcome'
import { assign, assignable, reviewFields, reviewRows } from './payslipReview'
import type { ReviewRow } from './payslipReview'

interface Props {
  onSubmit: (fields: ProfileFields) => Promise<boolean>
  onCancel: () => void
  loadProfile: () => Promise<Record<string, unknown>>
  disabled: boolean
}

// MIME types the server accepts (it also re-checks by file signature). Guides the file picker.
const ACCEPT = 'application/pdf,image/jpeg,image/png,image/webp'
const UPLOAD_HINT = 'מעלים צילום או PDF של התלוש. קוראים ממנו רק נתוני שכר והפרשות — לא שם, לא ת"ז ולא פרטי בנק. ' +
  'התלוש עצמו לא נשמר.'
const MANUAL_HINT = 'מלאו את הנתונים ידנית, סמנו את מה לשמור ואשרו. אפשר גם לבטל ולכתוב בצ׳אט.'
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
  const [profile, setProfile] = useState<Record<string, unknown>>({})
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // what the conversation already knows, to flag a payslip value that differs from it
  useEffect(() => {
    let alive = true
    loadProfile().then((p) => { if (alive) setProfile(p) }).catch(() => undefined)
    return () => { alive = false }
  }, [loadProfile])

  const pick = () => inputRef.current?.click()

  const onFile = async (file: File | undefined) => {
    if (!file || busy || disabled) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.extractPayslip(file)
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
  const manual = () => {
    setOutcome(null)
    setRows(reviewRows([], profile))
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
    if (Object.keys(built.errors).length) return
    if (!Object.keys(built.fields).length) {
      setError('לא סומן אף שדה לשמירה. סמנו לפחות שדה אחד או בטלו.')
      return
    }
    setBusy(true)
    const ok = await onSubmit(built.fields)
    setBusy(false)
    if (!ok) setError('השמירה נכשלה. נסו שוב.')
  }

  const working = busy || disabled
  const fileInput = (
    <input ref={inputRef} type="file" accept={ACCEPT} className="visually-hidden" tabIndex={-1}
           aria-hidden="true" onChange={(e) => void onFile(e.target.files?.[0])} />
  )

  if (step === 'pick' || step === 'failed') {
    const failed = step === 'failed' && outcome !== null
    return (
      <div className={`profile-form panel-card payslip-upload${failed ? ' is-failed' : ''}`} aria-label="העלאת תלוש">
        <header className="panel-card-head">
          <span className="panel-card-icon" aria-hidden="true"><Icon name={failed ? 'alert' : 'doc'} size={20} /></span>
          <div>
            <h3>{failed ? outcome.title : 'העלאת תלוש'}</h3>
            {failed
              ? <p className="field-error" role="alert">{outcome.message}</p>
              : <p className="form-hint">{UPLOAD_HINT}</p>}
          </div>
        </header>
        {fileInput}
        <div className={`dropzone${dragging ? ' is-over' : ''}${busy ? ' is-busy' : ''}`}
             onDragOver={(e) => { e.preventDefault(); if (!working) setDragging(true) }}
             onDragLeave={() => setDragging(false)}
             onDrop={(e) => { e.preventDefault(); setDragging(false); void onFile(e.dataTransfer.files?.[0]) }}>
          <span className="dropzone-art" aria-hidden="true">{busy ? <span className="scan" /> : <Icon name="upload" size={26} />}</span>
          {!busy && <span className="dropzone-text">אפשר לגרור לכאן קובץ PDF או תמונה</span>}
          <span className="dropzone-lock"><Icon name="lock" size={14} />התלוש עצמו לא נשמר</span>
        </div>
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="form-actions">
          <button type="button" className="chip chip-primary" disabled={working} onClick={pick}>
            {busy ? 'קורא את התלוש…' : !failed ? 'בחירת קובץ' : outcome.retry ? 'לנסות שוב' : 'לנסות קובץ אחר'}
          </button>
          {failed && (
            <button type="button" className="chip chip-quiet" disabled={working} onClick={manual}>מילוי ידני</button>
          )}
          <button type="button" className="chip chip-quiet" disabled={working} onClick={onCancel}>ביטול</button>
        </div>
      </div>
    )
  }

  const kept = rows.filter((r) => r.keep).length
  return (
    <form className="profile-form panel-card payslip-review" onSubmit={confirm} aria-label="סקירת נתוני התלוש">
      <header className="panel-card-head">
        <span className="panel-card-icon" aria-hidden="true"><Icon name={outcome ? 'doc' : 'form'} size={20} /></span>
        <div>
          <h3>{outcome ? outcome.title : 'מילוי ידני'}</h3>
          <p className="form-hint" role={outcome?.status === 'nothing_found' ? 'status' : undefined}>
            {outcome ? outcome.message : MANUAL_HINT}
          </p>
        </div>
      </header>
      {fileInput}
      <div className="form-grid review-grid">
        {rows.map((r) => (
          <div key={r.key} className={`form-field payslip-row payslip-${r.status}${r.keep ? ' is-kept' : ''}`} data-field={r.key}>
            <label htmlFor={`ps-${r.key}`}>
              {r.label}{r.unit && <span className="unit"> ({r.unit})</span>}
            </label>
            <div className="form-input-row">
              <input id={`ps-${r.key}`} inputMode="decimal" autoComplete="off" value={r.value}
                     disabled={working} aria-invalid={errors[r.key] ? true : undefined}
                     onChange={(e) => update(r.key, { value: e.target.value, keep: e.target.value.trim() !== '' })} />
              <label className="keep-toggle">
                <input type="checkbox" checked={r.keep} disabled={working} aria-label={`${r.label}: לשמור`}
                       onChange={(e) => update(r.key, { keep: e.target.checked })} />
                לשמור
              </label>
            </div>
            {r.source && <span className="field-source">{r.source}</span>}
            {r.note && <span className="field-note" role="note">{r.note}</span>}
            {r.known !== null && (
              <span className={r.differs ? 'field-note' : 'field-source'}>
                {r.differs ? `שונה ממה שרשמתם בשיחה: ${r.known}` : `בשיחה: ${r.known}`}
              </span>
            )}
            {errors[r.key] && <span className="field-error" role="alert">{errors[r.key]}</span>}
          </div>
        ))}
      </div>
      {info.length > 0 && (
        <section className="payslip-info" aria-label="נמצא בתלוש ולא נכנס לחישוב אוטומטית">
          <h4><Icon name="info" size={16} />נמצא בתלוש ולא נכנס לחישוב אוטומטית</h4>
          <ul>
            {info.map((i, n) => (
              <li key={`${i.key}-${n}`} data-info={i.key}>
                <span className="info-main"><strong>{i.source_label || i.label}</strong>: <bdi className="num">{infoValue(i)}</bdi></span>
                <span className="field-source"> {i.note}</span>
                {assignable(i, rows).map((k) => (
                  <button key={k} type="button" className="chip chip-quiet btn-sm" disabled={working}
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
