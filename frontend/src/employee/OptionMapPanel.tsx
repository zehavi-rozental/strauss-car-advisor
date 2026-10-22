// STRAUSS DRIVE 360°: every option the employee may choose, as the server calculated it. Display only: the
// numbers are the engine's, the order and filters only rearrange them, and nothing is chosen for the employee.
import { useState } from 'react'
import { createPortal } from 'react-dom'
import type { MapEntry, OptionMap, OptionResult } from '../api/types'
import { Money } from '../format/Amount'
import { Icon } from '../ui/Icon'
import { useDialog } from '../ui/useDialog'
import { driveLabel } from './labels'
import { STALE_NOTE } from './MessageList'
import {
  MAX_COMPARE, NET_ONLY_NOTE, STATUS_LABEL, asResults, filterValues, leadText, mapSections, toggleCompare,
} from './optionMap'
import type { MapFilter, MapSort } from './optionMap'
import { isBlocked, limitMessage } from './widgets'

interface Props {
  map: OptionMap
  onClose: () => void
  onCompare: (results: OptionResult[]) => void
}

function EntryCard({ entry, lead, picked, onToggle }:
  { entry: MapEntry; lead: boolean; picked: boolean; onToggle: () => void }) {
  const r = entry.result
  const calculated = entry.status === 'full' || entry.status === 'catalog_copay'
  const delta = calculated ? Number(r.delta_display) : 0
  return (
    <li className={`map-entry map-${entry.status}${lead ? ' map-lead' : ''}`} data-option={entry.option_id}>
      <div className="map-entry-head">
        <span className="map-name">{entry.label}</span>
        {lead && <span className="tag map-lead-tag">הנטו הגבוה ביותר</span>}
      </div>
      <div className="map-meta">
        {entry.group && <span className="chip-static">קבוצה {entry.group}</span>}
        {entry.drive && <span className="chip-static">{driveLabel(entry.drive)}</span>}
        <span className={`map-status status-${entry.status}`}>{STATUS_LABEL[entry.status]}</span>
      </div>
      {calculated && r.alternative && (
        <div className="map-figures">
          <span>נטו משוער <Money value={r.alternative.net_display} /></span>
          <span>מול היום <Money value={r.delta_display!} signed className={delta < 0 ? 'negative' : 'positive'} /></span>
        </div>
      )}
      {calculated && r.why && <p className="map-why">{r.why}</p>}
      {!calculated && r.reason && <p className="map-why">{r.reason}</p>}
      {calculated && (
        <button type="button" className={picked ? 'chip chip-on' : 'chip'} aria-pressed={picked} onClick={onToggle}>
          {picked ? 'נבחר להשוואה' : 'להשוואה'}
        </button>
      )}
    </li>
  )
}

export function OptionMapPanel({ map, onClose, onCompare }: Props) {
  const [sort, setSort] = useState<MapSort>('net')
  const [filter, setFilter] = useState<MapFilter>({ drive: null, group: null })
  const [picked, setPicked] = useState<string[]>([])
  // an option chosen while MAX_COMPARE are picked is not added; its button stays enabled and the tap says why.
  // The count changes on every refused tap, so the message is announced again each time.
  const [refused, setRefused] = useState(0)
  const toggle = (e: MapEntry) => {
    if (isBlocked(picked, e.option_id, MAX_COMPARE)) {
      setRefused((n) => n + 1)
      return
    }
    setRefused(0)
    setPicked((p) => toggleCompare(p, e))
  }
  const { ranked, waiting, notEligible } = mapSections(map, filter, sort)
  const { drives, groups } = filterValues(map)

  const closeRef = useDialog(onClose)      // Escape, focus, and the page under it held still (the design's dialogs)

  const card = (e: MapEntry) => (
    <EntryCard key={e.option_id} entry={e} lead={!map.stale && map.leaders.includes(e.option_id)}
               picked={picked.includes(e.option_id)} onToggle={() => toggle(e)} />
  )

  // rendered at the end of <body>, like the design's panels: above the top bar and the chat's stacking context
  return createPortal(
    <div className="panel-backdrop" onClick={onClose}>
      <aside className="comparison-panel option-map" role="dialog" aria-modal="true" aria-label="מפת האפשרויות שלכם"
             onClick={(e) => e.stopPropagation()}>
        <header className="panel-header">
          <div>
            <p className="panel-kicker">STRAUSS DRIVE 360°</p>
            <h2>מפת האפשרויות שלכם</h2>
          </div>
          <button ref={closeRef} type="button" className="panel-close" onClick={onClose} aria-label="סגירה">
            <Icon name="close" size={22} />
          </button>
        </header>
        <div className={map.stale ? 'panel-body result-stale' : 'panel-body'}>
          {map.stale && <p className="stale-note" role="status">{STALE_NOTE}</p>}
          <dl className="map-summary">
            {map.group && <div><dt>קבוצת זכאות</dt><dd>{map.group}</dd></div>}
            <div><dt>חלופות זכאיות</dt><dd>{map.counts.eligible}</dd></div>
            <div><dt>חושבו</dt><dd>{map.counts.calculated}</dd></div>
            <div><dt>ממתינות למידע</dt><dd>{map.counts.waiting}</dd></div>
          </dl>
          <p className="map-lead-line">{leadText(map)}</p>
          <p className="form-hint">{NET_ONLY_NOTE}</p>
          {map.counts.catalog_copay > 0 && (
            <p className="form-hint">ברכבים החדשים ההשתתפות לפי הקטלוג, עד לאישור נציג התחבורה.</p>
          )}

          <div className="map-controls">
            <label>מיון
              <select value={sort} onChange={(e) => setSort(e.target.value as MapSort)}>
                <option value="net">לפי נטו משוער</option>
                <option value="name">לפי שם</option>
              </select>
            </label>
            {drives.length > 1 && (
              <label>הנעה
                <select value={filter.drive ?? ''} onChange={(e) => setFilter((f) => ({ ...f, drive: e.target.value || null }))}>
                  <option value="">הכול</option>
                  {drives.map((d) => <option key={d} value={d}>{driveLabel(d)}</option>)}
                </select>
              </label>
            )}
            {groups.length > 1 && (
              <label>קבוצה
                <select value={filter.group ?? ''} onChange={(e) => setFilter((f) => ({ ...f, group: e.target.value || null }))}>
                  <option value="">הכול</option>
                  {groups.map((g) => <option key={g} value={g}>{g}</option>)}
                </select>
              </label>
            )}
          </div>

          <section aria-label="חלופות שחושבו">
            <h3 className="map-section">חושבו ({ranked.length})</h3>
            {ranked.length ? <ul className="map-list">{ranked.map(card)}</ul>
              : <p className="form-hint">אין חלופות מחושבות בסינון הזה.</p>}
          </section>
          {waiting.length > 0 && (
            <section aria-label="ממתינות למידע">
              <h3 className="map-section">ממתינות למידע ({waiting.length})</h3>
              <ul className="map-list">{waiting.map(card)}</ul>
            </section>
          )}
          {notEligible.length > 0 && (
            <section aria-label="לא זמינות">
              <h3 className="map-section">לא זמינות ({notEligible.length})</h3>
              <ul className="map-list">{notEligible.map(card)}</ul>
            </section>
          )}
          {map.excluded.length > 0 && (
            <details className="map-excluded">
              <summary>רכבים שאינם בזכאות שלכם ({map.excluded.length})</summary>
              <ul>{map.excluded.map((x) => <li key={x.label}><bdi>{x.label}</bdi>: {x.reason}</li>)}</ul>
            </details>
          )}
        </div>
        <div className="panel-actions">
          {refused > 0 && (
            <p key={refused} className="selection-limit" role="alert">
              <Icon name="info" size={18} />{limitMessage(MAX_COMPARE)}
            </p>
          )}
          <button type="button" className="chip chip-primary" disabled={picked.length === 0}
                  onClick={() => onCompare(asResults(map, picked))}>
            השוואה מפורטת{picked.length ? ` (${picked.length})` : ''}
          </button>
          <span className="form-hint">אפשר לבחור עד {MAX_COMPARE} חלופות להשוואה זו לצד זו. הרשימה המלאה נשארת כאן.</span>
        </div>
      </aside>
    </div>,
    document.body,
  )
}

/** The chat card: what was mapped and what leads, with the way into the full map. */
export function OptionMapSummary({ map, onOpen }: { map: OptionMap; onOpen: () => void }) {
  return (
    <article className={map.stale ? 'summary-card map-card result-stale' : 'summary-card map-card'}
             aria-label="מפת האפשרויות">
      {map.stale && <p className="stale-note" role="status">{STALE_NOTE}</p>}
      <h3 className="result-title">מפת האפשרויות שלכם</h3>
      <p>{map.counts.eligible} חלופות זכאיות · {map.counts.calculated} חושבו · {map.counts.waiting} ממתינות</p>
      <p className="map-lead-line">{leadText(map)}</p>
      <button type="button" className="table-button" onClick={onOpen}>פתיחת מפת האפשרויות</button>
    </article>
  )
}
