// Q2 as a form inside the chat, and "תיקון נתונים". One submit sends every changed field in one
// structured request ({fields}); the server validates and saves it without the LLM.
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import type { ProfileFields } from '../api/types'
import { FORM_FIELDS, buildFields, emptyValues, prefill } from './formFields'
import type { FormKey, FormValues } from './formFields'

interface Props {
  mode: 'q2' | 'correction'
  loadProfile: () => Promise<Record<string, unknown>>
  onSubmit: (fields: ProfileFields) => Promise<boolean>
  onCancel?: () => void
  disabled: boolean
}

export function ProfileForm({ mode, loadProfile, onSubmit, onCancel, disabled }: Props) {
  const [values, setValues] = useState<FormValues>(emptyValues)
  const [original, setOriginal] = useState<FormValues>(emptyValues)
  const [errors, setErrors] = useState<Partial<Record<FormKey, string>>>({})

  // known values come from the server; what the employee already typed is not overwritten
  useEffect(() => {
    let live = true
    loadProfile().then((p) => {
      if (!live) return
      const known = prefill(p)
      setOriginal(known)
      setValues((current) => Object.fromEntries(
        FORM_FIELDS.map((f) => [f.key, current[f.key] !== '' ? current[f.key] : known[f.key]])) as FormValues)
    }).catch(() => undefined)
    return () => { live = false }
  }, [loadProfile])

  const set = (key: FormKey, value: string) => {
    setValues((v) => ({ ...v, [key]: value }))
    setErrors((e) => ({ ...e, [key]: undefined }))
  }
  const { fields } = buildFields(values, original)
  const changed = Object.keys(fields).length

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const built = buildFields(values, original)
    setErrors(built.errors)
    if (Object.keys(built.errors).length || !Object.keys(built.fields).length) return
    await onSubmit(built.fields)
  }

  return (
    <form className="profile-form" onSubmit={submit} aria-label={mode === 'q2' ? 'פרטי שכר נוספים' : 'תיקון נתונים'}>
      <h3>{mode === 'q2' ? 'עוד כמה פרטים על השכר' : 'תיקון נתונים'}</h3>
      <p className="form-hint">
        {mode === 'q2'
          ? 'ממלאים מה שיודעים. אם אין, לוחצים "אין". אפשר גם להמשיך בכתיבה חופשית.'
          : 'משנים רק מה שצריך. הנטו יחושב מחדש.'}
      </p>
      <div className="form-grid">
        {FORM_FIELDS.map((f) => (
          <div key={f.key} className="form-field">
            <label htmlFor={`pf-${f.key}`}>{f.label}{f.unit && <span className="unit"> ({f.unit})</span>}</label>
            <div className="form-input-row">
              <input id={`pf-${f.key}`} inputMode="decimal" autoComplete="off" value={values[f.key]}
                     disabled={disabled} aria-invalid={errors[f.key] ? true : undefined}
                     onChange={(e) => set(f.key, e.target.value)} />
              <button type="button" className="chip chip-quiet" disabled={disabled}
                      aria-label={`${f.label}: אין`} onClick={() => set(f.key, '0')}>אין</button>
            </div>
            {errors[f.key] && <span className="field-error" role="alert">{errors[f.key]}</span>}
          </div>
        ))}
      </div>
      <div className="form-actions">
        <button type="submit" className="chip chip-primary" disabled={disabled || changed === 0}>שליחה</button>
        {onCancel && <button type="button" className="chip chip-quiet" disabled={disabled} onClick={onCancel}>ביטול</button>}
      </div>
    </form>
  )
}
