// Payslip upload, extraction, and the editable Hebrew review. Three steps, inside the chat:
//   1. pick a PDF/image of the payslip,
//   2. the server extracts ONLY the supported fields (never name / ID / base salary) and returns them,
//   3. the employee reviews every value, edits what is wrong, and explicitly confirms before anything is saved.
// The payslip file is never stored by the server; saving happens through the normal {fields} message,
// exactly like the manual form, only after this confirm.
import { useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { ApiError, api } from '../api/client'
import type { ExtractedField, ProfileFields } from '../api/types'
import { FORM_FIELDS, buildFields, emptyValues } from './formFields'
import type { FormKey, FormValues } from './formFields'

interface Props {
  onSubmit: (fields: ProfileFields) => Promise<boolean>
  onCancel: () => void
  disabled: boolean
}

// MIME types the server accepts (it also re-checks by file signature). Guides the file picker.
const ACCEPT = 'application/pdf,image/jpeg,image/png,image/webp'
const UPLOAD_HINT = 'מעלים צילום או PDF של התלוש. קוראים ממנו רק את השדות שבטופס — לא שם, לא ת"ז, לא שכר הבסיס. ' +
  'התלוש עצמו לא נשמר.'
const REVIEW_HINT = 'אלה הערכים שזוהו מהתלוש. בדקו כל שדה, תקנו מה שצריך, ואז אשרו. ' +
  'שדה שמסומן "נא לבדוק" לא זוהה בוודאות.'
const NOTHING_FOUND = 'לא זוהו ערכים מהתלוש. אפשר למלא ידנית למטה ולאשר, או לבטל ולכתוב בצ׳אט.'

type Step = 'pick' | 'review'

/** The extracted field value as an input string (null → empty; counts shown as integers). */
function toInput(f: ExtractedField): string {
  if (f.value === null || f.value === undefined || f.value === '') return ''
  const n = Number(f.value)
  if (!Number.isFinite(n)) return ''
  return String(n)
}

function prefillFromExtraction(fields: ExtractedField[]): FormValues {
  const out = emptyValues()
  const byField = new Map(fields.map((f) => [f.field, f]))
  for (const spec of FORM_FIELDS) {
    const ex = byField.get(spec.key)
    if (ex) out[spec.key] = toInput(ex)
  }
  return out
}

export function PayslipUpload({ onSubmit, onCancel, disabled }: Props) {
  const [step, setStep] = useState<Step>('pick')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [extracted, setExtracted] = useState<ExtractedField[]>([])
  const [values, setValues] = useState<FormValues>(emptyValues)
  const [errors, setErrors] = useState<Partial<Record<FormKey, string>>>({})
  const inputRef = useRef<HTMLInputElement>(null)

  const noteFor = (key: FormKey): string => {
    const ex = extracted.find((f) => f.field === key)
    if (!ex || ex.value === null) return ''
    return ex.confident ? '' : (ex.note || 'לא ודאי — נא לבדוק')
  }

  const pick = () => inputRef.current?.click()

  const onFile = async (file: File | undefined) => {
    if (!file || busy || disabled) return
    setBusy(true)
    setError(null)
    try {
      const result = await api.extractPayslip(file)
      setExtracted(result.fields)
      setValues(prefillFromExtraction(result.fields))
      setErrors({})
      setStep('review')
    } catch (e) {
      setError(e instanceof ApiError ? e.message_he : 'ההעלאה נכשלה. נסו שוב או מלאו ידנית.')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''   // allow re-picking the same file
    }
  }

  const set = (key: FormKey, value: string) => {
    setValues((v) => ({ ...v, [key]: value }))
    setErrors((prev) => ({ ...prev, [key]: undefined }))
  }

  const confirm = async (e: FormEvent) => {
    e.preventDefault()
    const built = buildFields(values)
    setErrors(built.errors)
    if (Object.keys(built.errors).length) return
    if (!Object.keys(built.fields).length) {
      setError('אין מה לשמור. מלאו לפחות שדה אחד או בטלו.')
      return
    }
    setBusy(true)
    const ok = await onSubmit(built.fields)
    setBusy(false)
    if (!ok) setError('השמירה נכשלה. נסו שוב.')
  }

  const working = busy || disabled
  const nothingFound = step === 'review' && extracted.every((f) => f.value === null)

  if (step === 'pick') {
    return (
      <div className="profile-form payslip-upload" aria-label="העלאת תלוש">
        <h3>העלאת תלוש</h3>
        <p className="form-hint">{UPLOAD_HINT}</p>
        <input ref={inputRef} type="file" accept={ACCEPT} className="visually-hidden" tabIndex={-1}
               aria-hidden="true" onChange={(e) => void onFile(e.target.files?.[0])} />
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="form-actions">
          <button type="button" className="chip chip-primary" disabled={working} onClick={pick}>
            {busy ? 'קורא את התלוש…' : 'בחירת קובץ'}
          </button>
          <button type="button" className="chip chip-quiet" disabled={working} onClick={onCancel}>ביטול</button>
        </div>
      </div>
    )
  }

  return (
    <form className="profile-form payslip-review" onSubmit={confirm} aria-label="סקירת נתוני התלוש">
      <h3>בדיקת הנתונים מהתלוש</h3>
      <p className="form-hint">{nothingFound ? NOTHING_FOUND : REVIEW_HINT}</p>
      <div className="form-grid">
        {FORM_FIELDS.map((f) => {
          const note = noteFor(f.key)
          return (
            <div key={f.key} className="form-field">
              <label htmlFor={`ps-${f.key}`}>
                {f.label}{f.unit && <span className="unit"> ({f.unit})</span>}
              </label>
              <div className="form-input-row">
                <input id={`ps-${f.key}`} inputMode="decimal" autoComplete="off" value={values[f.key]}
                       disabled={working} aria-invalid={errors[f.key] ? true : undefined}
                       onChange={(e) => set(f.key, e.target.value)} />
                <button type="button" className="chip chip-quiet" disabled={working}
                        aria-label={`${f.label}: אין`} onClick={() => set(f.key, '0')}>אין</button>
              </div>
              {note && <span className="field-note" role="note">{note}</span>}
              {errors[f.key] && <span className="field-error" role="alert">{errors[f.key]}</span>}
            </div>
          )
        })}
      </div>
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="form-actions">
        <button type="submit" className="chip chip-primary" disabled={working}>
          {busy ? 'שומר…' : 'אישור ושמירה'}
        </button>
        <button type="button" className="chip chip-quiet" disabled={working} onClick={onCancel}>ביטול</button>
      </div>
    </form>
  )
}
