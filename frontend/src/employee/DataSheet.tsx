// What the system knows, in the chat: a thin strip with what this stage is about, and one sheet ("הנתונים שלי") where every value
// shows where it came from and can be changed on its own. No page and no dashboard. Everything shown comes from the server's
// `facts`; a saved change goes through the one {fields} gate, and the server recalculates.
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { CarSearchItem, FactItem, Facts, Privacy, ProfileFields } from '../api/types'
import { Icon } from '../ui/Icon'
import { useDialog } from '../ui/useDialog'
import { parseNumber } from './formFields'
import type { SendResult } from './useConversation'

export const MY_DATA_LABEL = 'הנתונים שלי'
const SECTIONS: [FactItem['section'], string][] = [['eligibility', 'זכאות'], ['pay', 'שכר והפרשות'], ['car', 'הרכב של היום'],
  ['results', 'חישוב']]
const UNITS: Record<string, string> = { money: '₪', percent: '%' }
const FIELD_OF: Record<string, keyof ProfileFields> = { group: 'eligibility_group' }

export function ContextStrip({ facts, onOpen }: { facts: Facts | null; onOpen: (key?: string) => void }) {
  if (!facts) return null
  const items = facts.strip.map((k) => facts.items.find((i) => i.key === k)).filter((i): i is FactItem => Boolean(i))
  return (
    <div className="context-strip" role="group" aria-label="הנתונים שמשמשים לחישוב">
      <button type="button" className="context-all" onClick={() => onOpen()}>
        <Icon name="form" size={16} />{MY_DATA_LABEL}
        {facts.missing.length > 0 && <span className="context-missing">{facts.missing.length} חסרים</span>}
      </button>
      {items.map((i) => (
        <button key={i.key} type="button" className="context-chip" onClick={() => onOpen(i.key)}
                aria-label={`${i.label}: ${i.display}. לעדכון`}>
          <span className="context-chip-label">{i.label}</span>
          <bdi className="context-chip-value">{i.display}</bdi>
        </button>
      ))}
    </div>
  )
}

interface EditorProps {
  item: FactItem
  disabled: boolean
  onSave: (fields: ProfileFields, display: string) => Promise<SendResult>
  onCheck: (text: string) => Promise<boolean>
  onDone: () => void
  /** saved or checked: the sheet closes, so that the card in the chat is seen at once */
  onSaved: () => void
}

/** One value, on its own. The server validates and recalculates; here only what was typed is read. */
function FieldEditor({ item, disabled, onSave, onCheck, onDone, onSaved }: EditorProps) {
  const numeric = ['money', 'percent', 'count', 'integer'].includes(item.edit ?? '')
  const [value, setValue] = useState(item.value ?? '')
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [cars, setCars] = useState<CarSearchItem[]>([])
  useEffect(() => {
    if (item.edit !== 'car' || query.trim().length < 2) { setCars([]); return }
    let live = true
    const t = window.setTimeout(() => {
      api.searchCars(query).then((found) => { if (live) setCars(found) }).catch(() => { if (live) setCars([]) })
    }, 250)
    return () => { live = false; window.clearTimeout(t) }
  }, [item.edit, query])

  const field = FIELD_OF[item.key] ?? (item.key as keyof ProfileFields)
  const save = async (raw: string | number, shown: string) => {
    setError(null)
    const sent = await onSave({ [field]: raw } as ProfileFields, shown)
    if (sent.ok) onSaved()
    else setError(sent.errors[field] ?? sent.errors[item.key] ?? null)
  }
  const number = () => {
    const parsed = parseNumber(value.replace(/,/g, ''), item.edit === 'integer')
    if (parsed === null) setError('כתבו מספר, בלי אותיות.')
    return parsed
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!numeric) return
    const parsed = number()
    if (parsed !== null) void save(parsed, `${item.label} ${value}`)
  }
  const check = async () => {
    if (number() === null) return
    if (await onCheck(`מה יקרה אם ${item.label} ${value.replace(/,/g, '')}${item.edit === 'percent' ? '%' : ''}?`)) onSaved()
  }

  if (item.edit === 'group' || item.edit === 'occupation') {
    return (
      <div className="field-editor">
        <div className="field-editor-choices" role="group" aria-label={item.label}>
          {(item.choices ?? []).map((c) => (
            <button key={c} type="button" className="chip" disabled={disabled} aria-pressed={c === item.value}
                    onClick={() => void save(c, `${item.label}: ${c}`)}>
              {item.edit === 'group' ? `קבוצה ${c}` : c}
            </button>
          ))}
        </div>
        {error && <p className="field-error" role="alert">{error}</p>}
        <button type="button" className="chip chip-quiet btn-sm" onClick={onDone}>ביטול</button>
      </div>
    )
  }
  if (item.edit === 'car') {
    return (
      <div className="field-editor">
        <label className="form-field">
          <span>דגם הרכב</span>
          <input value={query} disabled={disabled} autoComplete="off" onChange={(e) => setQuery(e.target.value)}
                 placeholder="למשל: קורולה" />
        </label>
        <div className="field-editor-choices">
          {cars.map((c) => (
            <button key={c.id} type="button" className="chip" disabled={disabled}
                    onClick={() => void save(c.id, `הרכב שלי היום: ${c.label}`)}><bdi>{c.label}</bdi></button>
          ))}
          <button type="button" className="chip chip-quiet" disabled={disabled}
                  onClick={() => void save('no_car', 'אין לי רכב צמוד')}>אין לי רכב צמוד</button>
        </div>
        {error && <p className="field-error" role="alert">{error}</p>}
        <button type="button" className="chip chip-quiet btn-sm" onClick={onDone}>ביטול</button>
      </div>
    )
  }
  return (
    <form className="field-editor" onSubmit={submit}>
      <label className="form-field">
        <span>{item.label}{UNITS[item.edit ?? ''] && <span className="unit"> ({UNITS[item.edit ?? '']})</span>}</span>
        <input inputMode="decimal" autoComplete="off" autoFocus value={value} disabled={disabled}
               aria-invalid={error ? true : undefined} onChange={(e) => { setValue(e.target.value); setError(null) }} />
      </label>
      {item.min != null && item.max != null && (
        <p className="field-editor-hint">הטווח האפשרי: <bdi>{Number(item.min).toLocaleString('he-IL')}–{Number(item.max).toLocaleString('he-IL')}</bdi></p>
      )}
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="field-editor-actions">
        <button type="submit" className="chip chip-primary" disabled={disabled || !value.trim() || value === item.value}>לעדכן</button>
        {item.value !== null && (
          <button type="button" className="chip" disabled={disabled || !value.trim() || value === item.value}
                  onClick={() => void check()}>רק לבדוק</button>
        )}
        <button type="button" className="chip chip-quiet" onClick={onDone}>ביטול</button>
      </div>
    </form>
  )
}

function Row({ item, editing, onEdit }: { item: FactItem; editing: boolean; onEdit: () => void }) {
  return (
    <div className={`data-row is-${item.status}`}>
      <div className="data-row-main">
        <span className="data-row-label">{item.label}</span>
        {item.value !== null
          ? <bdi className="data-row-value">{item.display}</bdi>
          : <span className="data-row-empty">{item.status_label || 'חסר'}</span>}
      </div>
      <div className="data-row-tags">
        {item.source_label && <span className="data-source">{item.source_label}</span>}
        {item.value !== null && item.status_label && <span className={`tag tag-${item.status}`}>{item.status_label}</span>}
        {!item.used_in_calc && <span className="data-source">לא נכנס לחישוב</span>}
      </div>
      {item.note && <p className="data-row-note">{item.note}</p>}
      {item.edit && !editing && (
        <button type="button" className="link-inline data-row-edit" onClick={onEdit} aria-label={`לעדכן: ${item.label}`}>
          <Icon name="edit" size={14} />לעדכן
        </button>
      )}
    </div>
  )
}

interface SheetProps {
  facts: Facts
  privacy: Privacy | null
  focus?: string
  busy: boolean
  onClose: () => void
  onSave: (fields: ProfileFields, display: string) => Promise<SendResult>
  onCheck: (text: string) => Promise<boolean>
  onSelection: () => void
  onDelete: () => void
  onWithdrawAi: () => void
}

export function DataSheet({ facts, privacy, focus, busy, onClose, onSave, onCheck, onSelection, onDelete, onWithdrawAi }: SheetProps) {
  const closeRef = useDialog(onClose)
  const [editing, setEditing] = useState<string | null>(focus ?? null)
  const [one, setOne] = useState(Boolean(focus))     // opened from the strip: only this value, the rest one tap away
  const edit = (item: FactItem) => { if (item.edit === 'selection') onSelection(); else setEditing(item.key) }
  const focused = one && focus ? facts.items.find((i) => i.key === focus) : undefined
  const done = () => { if (one) onClose(); else setEditing(null) }
  const row = (item: FactItem) => (
    <div key={item.key} id={`fact-${item.key}`}>
      <Row item={item} editing={editing === item.key} onEdit={() => edit(item)} />
      {editing === item.key && item.edit && item.edit !== 'selection' && (
        <FieldEditor item={item} disabled={busy} onSave={onSave} onCheck={onCheck} onDone={done} onSaved={onClose} />
      )}
    </div>
  )
  return createPortal(
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="comparison-panel data-sheet" role="dialog" aria-modal="true" aria-label={MY_DATA_LABEL}
             onClick={(e) => e.stopPropagation()}>
        <header className="panel-header">
          <div>
            <p className="panel-kicker">מה רשום ומה משמש לחישוב</p>
            <h2>{MY_DATA_LABEL}</h2>
          </div>
          <button ref={closeRef} type="button" className="panel-close" onClick={onClose} aria-label="סגירה">
            <Icon name="close" size={22} />
          </button>
        </header>
        <div className="panel-body">
          {focused ? (
            <section className="data-section">
              {row(focused)}
              <button type="button" className="link-inline" onClick={() => setOne(false)}>לכל הנתונים</button>
            </section>
          ) : (
            <>
              {SECTIONS.map(([section, title]) => {
                const items = facts.items.filter((i) => i.section === section)
                return items.length > 0 && (
                  <section key={section} className="data-section">
                    <h3 className="section-title">{title}</h3>
                    {items.map(row)}
                  </section>
                )
              })}
              {facts.assumptions.length > 0 && (
                <section className="data-section">
                  <h3 className="section-title">הנחות החישוב</h3>
                  <ul className="data-assumptions">{facts.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
                </section>
              )}
              <section className="data-section">
                <h3 className="section-title">פרטיות</h3>
                <PrivacyLines privacy={privacy} />
                <div className="field-editor-actions">
                  {privacy?.ai.accepted && (
                    <button type="button" className="chip chip-quiet" disabled={busy} onClick={onWithdrawAi}>להפסיק עיבוד ב-AI</button>
                  )}
                  <button type="button" className="chip chip-danger" disabled={busy} onClick={onDelete}>
                    <Icon name="close" size={16} />מחיקת השיחה והנתונים
                  </button>
                </div>
              </section>
            </>
          )}
        </div>
      </aside>
    </div>,
    document.body,
  )
}

export function PrivacyLines({ privacy }: { privacy: Privacy | null }) {
  return (
    <ul className="data-assumptions">
      <li>הפרטים נשמרים בשרת לצורך השיחה בלבד, ולא נדרשת הרשמה.</li>
      {privacy && privacy.retention_days > 0 && <li>שיחה נמחקת אוטומטית אחרי {privacy.retention_days} ימים ללא פעילות.</li>}
      {privacy && privacy.idle_minutes > 0 && (
        <li>אחרי {privacy.idle_minutes} דקות ללא פעילות השיחה ננעלת בדפדפן הזה. במחשב משותף כדאי למחוק בסיום.</li>
      )}
      {privacy?.ai.available && (
        <li>{privacy.ai.accepted
          ? 'הודעות השיחה והנתונים שמסרתם מעובדים ב-Google Gemini, בלי פרטי קשר ובלי מספרי זהות.'
          : 'השיחה פועלת בלי AI: שום הודעה לא נשלחת ל-Google Gemini.'}</li>
      )}
      <li>מחיקה מסירה את השיחה מהשרת. עותקי גיבוי ומה שכבר עובד אצל ספק ה-AI נשמרים לפי התקופות שלהם.</li>
    </ul>
  )
}

/** Said before anything is sent to the model. Nothing is preselected; without an answer the conversation works with no model. */
export function PrivacyNotice({ privacy, disabled, onAccept, onHide }:
  { privacy: Privacy; disabled: boolean; onAccept: () => void; onHide: () => void }) {
  return (
    <aside className="privacy-notice" aria-label="פרטיות">
      {privacy.demo && <p className="privacy-demo"><Icon name="alert" size={16} />זו סביבת הדגמה. אל תזינו כאן נתוני שכר אמיתיים.</p>}
      <p>
        כדי להבין כתיבה חופשית, ההודעות שלכם והנתונים שמסרתם נשלחים לעיבוד ב-Google Gemini, בלי פרטי קשר ובלי מספרי זהות.
        החישובים עצמם נעשים כאן. בלי אישור, השיחה ממשיכה בלי AI.
      </p>
      <div className="field-editor-actions">
        <button type="button" className="chip chip-primary" disabled={disabled} onClick={onAccept}>מאשרים עיבוד ב-AI</button>
        <button type="button" className="chip chip-quiet" onClick={onHide}>להמשיך בלי AI</button>
      </div>
    </aside>
  )
}

/** A question that deletes: said plainly, in the page and not as a browser pop-up. */
export function ConfirmDialog({ title, text, confirm, danger = false, busy, onConfirm, onCancel }:
  { title: string; text: string; confirm: string; danger?: boolean; busy: boolean; onConfirm: () => void; onCancel: () => void }) {
  const closeRef = useDialog(onCancel)
  return createPortal(
    <div className="panel-backdrop" onClick={onCancel}>
      <div className="confirm-dialog" role="alertdialog" aria-modal="true" aria-label={title} onClick={(e) => e.stopPropagation()}>
        <h2>{title}</h2>
        <p>{text}</p>
        <div className="field-editor-actions">
          <button type="button" className={danger ? 'chip chip-danger' : 'chip chip-primary'} disabled={busy} onClick={onConfirm}>{confirm}</button>
          <button ref={closeRef} type="button" className="chip chip-quiet" onClick={onCancel}>ביטול</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

/** A conversation saved in this browser, or one that locked: nothing of it is shown until someone chooses. */
export function ResumeCard({ locked, updatedAt, busy, error, onResume, onDelete }:
  { locked: boolean; updatedAt?: string; busy: boolean; error: string | null; onResume: () => void; onDelete: () => void }) {
  const when = updatedAt ? new Date(updatedAt).toLocaleString('he-IL', { dateStyle: 'short', timeStyle: 'short' }) : null
  return (
    <div className="resume-screen">
      <section className="panel-card resume-card" aria-label="שיחה שמורה">
        <header className="panel-card-head">
          <span className="panel-card-icon" aria-hidden="true"><Icon name="lock" size={20} /></span>
          <div>
            <h3>{locked ? 'השיחה הקודמת ננעלה' : 'יש בדפדפן הזה שיחה שמורה'}</h3>
            <p className="form-hint">
              {locked
                ? 'עבר זמן ללא פעילות, ולכן הפרטים לא מוצגים. אם אימתם מייל, הקישור מהמייל פותח את השיחה מחדש. אפשר גם למחוק אותה ולהתחיל שיחה חדשה.'
                : `כדי להגן על הפרטים, הם לא מוצגים עד שתבחרו.${when ? ` עדכון אחרון: ${when}.` : ''} אם זו לא השיחה שלכם, מחקו והתחילו מחדש.`}
            </p>
          </div>
        </header>
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="field-editor-actions">
          {!locked && <button type="button" className="chip chip-primary" disabled={busy} onClick={onResume}>להמשיך את השיחה</button>}
          <button type="button" className={locked ? 'chip chip-primary' : 'chip chip-danger'} disabled={busy} onClick={onDelete}>
            למחוק ולהתחיל שיחה חדשה
          </button>
        </div>
      </section>
    </div>
  )
}
