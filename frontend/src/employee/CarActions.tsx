// The three ways into the cars, in one compact place under the alternatives question and under the results (1/11):
// choose up to 3 for the comparison in the chat, the 3 with the highest net (also into the chat), or every car by
// net in the option map (to look at; the chat's comparison does not change). The same three, the same names,
// wherever they show. Nothing here calculates: each one sends what the chat already understands.
import { useState } from 'react'
import { Icon } from '../ui/Icon'

export const CHOOSE_LABEL = 'לבחור רכבים להשוואה'
export const CHANGE_LABEL = 'לשנות בחירה'
export const RANK_LABEL = '3 המשתלמים ביותר'
export const MAP_LABEL = 'כל הרכבים לפי נטו'

/** What the full ranking still needs, and how to give it (never a dead button). */
export function mapMissingText(missing: string[]): string {
  return missing.length
    ? `כדי לדרג את כל הרכבים חסרים לי עוד: ${missing.join(', ')}. אפשר לכתוב אותם כאן בצ'אט`
    : 'הדירוג של כל הרכבים יהיה זמין אחרי תשובת נציג התחבורה.'
}

interface Props {
  /** the cars in the chat's comparison now (empty before results) */
  selected: string[]
  /** the alternatives question is open: choosing and ranking are possible */
  canChoose: boolean
  /** the server offers the ranking (the options question's "rank" quick reply) */
  canRank: boolean
  /** the map can be built now / is already built */
  mapReady: boolean
  mapExists: boolean
  /** what the map still needs, in Hebrew (from the server) */
  mapMissing: string[]
  disabled: boolean
  onChoose: () => void
  onRank: () => void
  onMap: () => void
  /** the data form, to fill in what is missing */
  onCompleteData?: () => void
}

export function CarActions({ selected, canChoose, canRank, mapReady, mapExists, mapMissing, disabled,
                             onChoose, onRank, onMap, onCompleteData }: Props) {
  // the map cannot be built yet: the click says what is missing and how to give it, never a dead button
  const [askedMap, setAskedMap] = useState(false)
  const mapOpenable = mapReady || mapExists
  return (
    <section className="car-actions" aria-label="הרכבים שלכם">
      {selected.length > 0 && (
        <p className="car-actions-current">
          <Icon name="grid" size={16} />בהשוואה בצ'אט: {selected.join(', ')}
        </p>
      )}
      <div className="car-actions-row">
        {canChoose && (
          <button type="button" className="chip" disabled={disabled} onClick={onChoose}
                  title="סמנו עד 3 רכבים, והנטו של כל אחד יחושב כאן בצ'אט">
            <Icon name={selected.length ? 'swap' : 'grid'} size={16} />{selected.length ? CHANGE_LABEL : CHOOSE_LABEL}
          </button>
        )}
        {canChoose && canRank && (
          <button type="button" className="chip" disabled={disabled} onClick={onRank}
                  title="3 הרכבים שמשאירים לכם את הנטו הגבוה ביותר, כהשוואה כאן בצ'אט">
            <Icon name="trendUp" size={16} />{RANK_LABEL}
          </button>
        )}
        <button type="button" className="chip" disabled={disabled} aria-expanded={!mapOpenable ? askedMap : undefined}
                onClick={() => (mapOpenable ? onMap() : setAskedMap(true))}
                title="כל הרכבים שאתם זכאים להם, מדורגים לפי נטו, לעיון. ההשוואה בצ'אט לא משתנה">
          <Icon name="table" size={16} />{MAP_LABEL}
        </button>
      </div>
      {askedMap && !mapOpenable && (
        <p className="car-actions-missing" role="status">
          {mapMissingText(mapMissing)}
          {mapMissing.length > 0 && (
            <>{onCompleteData ? <>, או <button type="button" className="link-inline" disabled={disabled}
                                             onClick={onCompleteData}>למלא בטופס</button></> : null}.</>
          )}
        </p>
      )}
    </section>
  )
}
