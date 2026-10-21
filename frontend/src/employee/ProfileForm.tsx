// Q2 as a form inside the chat, and "תיקון נתונים". One submit sends every changed field in one
// structured request ({fields}); the server validates and saves it without the LLM, and recalculates a result.
// The correction also offers the base salary (the {fields} contract accepts it): a salary typed as 15,000 and
// corrected here to 16,000 is saved and the net is calculated again, through the server's own path.
import { useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import type { ProfileFields } from '../api/types'
import { Icon } from '../ui/Icon'
import type { SendResult } from './useConversation'
import { CORRECTION_FIELDS, FORM_FIELDS, buildFields, emptyValues, prefill } from './formFields'
import type { FormKey, FormValues } from './formFields'

interface Props {
  mode: 'q2' | 'correction'
  loadProfile: () => Promise<Record<string, unknown>>
  onSubmit: (fields: ProfileFields) => Promise<SendResult>
  onCancel?: () => void
  disabled: boolean
}

export function ProfileForm({ mode, loadProfile, onSubmit, onCancel, disabled }: Props) {
  const specs = mode === 'correction' ? CORRECTION_FIELDS : FORM_FIELDS
  const blank = useMemo(() => emptyValues(specs), [specs])
  const [values, setValues] = useState<FormValues>(blank)
  const [original, setOriginal] = useState<FormValues>(blank)
  const [errors, setErrors] = useState<Partial<Record<FormKey, string>>>({})
  const [prefillFailed, setPrefillFailed] = useState(false)

  // known values come from the server; what the employee already typed is not overwritten
  useEffect(() => {
    let live = true
    loadProfile().then((p) => {
      if (!live) return
      const known = prefill(p, specs)
      setOriginal(known)
      setValues((current) => Object.fromEntries(
        specs.map((f) => [f.key, current[f.key] ? current[f.key] : known[f.key]])) as FormValues)
    }).catch(() => { if (live) setPrefillFailed(true) })   // said on screen, never an unexplained empty form
    return () => { live = false }
  }, [loadProfile, specs])

  const set = (key: FormKey, value: string) => {
    setValues((v) => ({ ...v, [key]: value }))
    setErrors((e) => ({ ...e, [key]: undefined }))
  }
  const { fields } = buildFields(values, original, specs)
  const changed = Object.keys(fields).length

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const built = buildFields(values, original, specs)
    setErrors(built.errors)
    if (Object.keys(built.errors).length || !Object.keys(built.fields).length) return
    const sent = await onSubmit(built.fields)
    // all or nothing: nothing was saved, everything typed stays, and each refused field says why
    if (!sent.ok) setErrors(sent.errors as Partial<Record<FormKey, string>>)
  }

  return (
    <form className={`profile-form panel-card is-${mode}`} onSubmit={submit} aria-label={mode === 'q2' ? 'פרטי שכר נוספים' : 'תיקון נתונים'}>
      <header className="panel-card-head">
        <span className="panel-card-icon" aria-hidden="true"><Icon name={mode === 'q2' ? 'form' : 'edit'} size={20} /></span>
        <div>
          <h3>{mode === 'q2' ? 'עוד כמה פרטים על השכר' : 'תיקון נתונים'}</h3>
          <p className="form-hint">
            {mode === 'q2'
              ? 'ממלאים מה שיודעים. אם אין, לוחצים "אין". אפשר גם להמשיך בכתיבה חופשית.'
              : 'משנים רק מה שצריך. הנטו יחושב מחדש.'}
          </p>
        </div>
      </header>
      {prefillFailed && (
        <p className="field-error" role="alert">
          לא הצלחתי לטעון את הערכים שכבר רשומים. מה שתמלאו יישלח; שדה ריק לא משנה דבר.
        </p>
      )}
      <div className="form-grid">
        {specs.map((f) => {
          const edited = mode === 'correction' && (values[f.key] ?? '') !== (original[f.key] ?? '')
          return (
            <div key={f.key} className={`form-field${edited ? ' is-edited' : ''}`}>
              <label htmlFor={`pf-${f.key}`}>{f.label}{f.unit && <span className="unit"> ({f.unit})</span>}</label>
              <div className="form-input-row">
                <input id={`pf-${f.key}`} inputMode="decimal" autoComplete="off" value={values[f.key] ?? ''}
                       disabled={disabled} aria-invalid={errors[f.key] ? true : undefined}
                       onChange={(e) => set(f.key, e.target.value)} />
                {f.key !== 'base_salary' && (
                  <button type="button" className="chip chip-quiet btn-sm" disabled={disabled}
                          aria-label={`${f.label}: אין`} onClick={() => set(f.key, '0')}>אין</button>
                )}
              </div>
              {errors[f.key] && <span className="field-error" role="alert">{errors[f.key]}</span>}
            </div>
          )
        })}
      </div>
      <div className="form-actions">
        <button type="submit" className="chip chip-primary" disabled={disabled || changed === 0}>
          שליחה{changed > 0 && <span className="count-badge num" aria-hidden="true">{changed}</span>}
        </button>
        {onCancel && <button type="button" className="chip chip-quiet" disabled={disabled} onClick={onCancel}>ביטול</button>}
      </div>
    </form>
  )
}
