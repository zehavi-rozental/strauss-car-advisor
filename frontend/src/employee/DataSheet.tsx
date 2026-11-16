// What the system knows, in the chat: one button ("הנתונים שלי", with how many values are missing) and one sheet where every value
// shows where it came from and can be changed on its own. Everything shown comes from the server's `facts`; a saved change goes
// through the one {fields} gate, and the server recalculates. Ported from feature/integration-to-main (9/11), without the
// "רק לבדוק" check, the privacy lines and the deletion, which the hotfix does not carry.
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../api/client'
import type { CarSearchItem, FactItem, Facts, ProfileFields } from '../api/types'
import { Icon } from '../ui/Icon'
import { useDialog } from '../ui/useDialog'
import { parseNumber } from './formFields'
import type { SendResult } from './useConversation'

export const MY_DATA_LABEL = 'הנתונים שלי'
export const EDIT_DATA_LABEL = 'עריכת הנתונים שלי'
const SECTIONS: [FactItem['section'], string][] = [['eligibility', 'זכאות'], ['pay', 'שכר והפרשות'], ['car', 'הרכב של היום'],
  ['results', 'חישוב']]
const UNITS: Record<string, string> = { money: '₪', percent: '%' }
const FIELD_OF: Record<string, keyof ProfileFields> = { group: 'eligibility_group' }

/** One entry point to what is known, under the blue band on the end side: view, change one value, fill a missing one.
 *  The count says how many values are still missing. */
export function MyDataButton({ facts, onOpen }: { facts: Facts | null; onOpen: (key?: string) => void }) {
  if (!facts?.items) return null                 // nothing said yet (or an older server): no button rather than a broken one
  const missing = facts.missing?.length ?? 0
  return (
    <button type="button" className="my-data-edit" onClick={() => onOpen()}
            aria-label={missing ? `${EDIT_DATA_LABEL}, ${missing} חסרים` : EDIT_DATA_LABEL}>
      <span className="my-data-icon" aria-hidden="true"><Icon name="edit" size={16} /></span>
      <span className="my-data-text">
        <span className="my-data-title">{EDIT_DATA_LABEL}</span>
        <span className="my-data-sub">עדכון וחישוב מחדש</span>
      </span>
      {missing > 0 && <span className="my-data-missing" aria-hidden="true">{missing} חסרים</span>}
    </button>
  )
}

interface EditorProps {
  item: FactItem
  disabled: boolean
  onSave: (fields: ProfileFields, display: string) => Promise<SendResult>
  onDone: () => void
  /** saved: the sheet closes, so that the reply in the chat is seen at once */
  onSaved: () => void
}

/** One value, on its own. The server validates and recalculates; here only what was typed is read. */
function FieldEditor({ item, disabled, onSave, onDone, onSaved }: EditorProps) {
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
    else setError(sent.errors[field] ?? sent.errors[item.key] ?? 'הערך לא נשמר. נסו שוב.')
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!numeric) return
    const parsed = parseNumber(value.replace(/,/g, ''), item.edit === 'integer')
    if (parsed === null) { setError('כתבו מספר, בלי אותיות.'); return }
    void save(parsed, `${item.label} ${value}`)
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
  focus?: string
  busy: boolean
  onClose: () => void
  onSave: (fields: ProfileFields, display: string) => Promise<SendResult>
  onSelection: () => void
}

export function DataSheet({ facts, focus, busy, onClose, onSave, onSelection }: SheetProps) {
  const closeRef = useDialog(onClose)
  const [editing, setEditing] = useState<string | null>(focus ?? null)
  const edit = (item: FactItem) => { if (item.edit === 'selection') onSelection(); else setEditing(item.key) }
  const row = (item: FactItem) => (
    <div key={item.key} id={`fact-${item.key}`}>
      <Row item={item} editing={editing === item.key} onEdit={() => edit(item)} />
      {editing === item.key && item.edit && item.edit !== 'selection' && (
        <FieldEditor item={item} disabled={busy} onSave={onSave} onDone={() => setEditing(null)} onSaved={onClose} />
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
        </div>
      </aside>
    </div>,
    document.body,
  )
}
