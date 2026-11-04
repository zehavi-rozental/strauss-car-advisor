// What a change did, or what a check would do: only the engine's figures from the server, before and after. The receipt stays
// with its message as history; a check says first that nothing was saved. Nothing is calculated here.
import type { CardOption, ChangeRow, MessageCard } from '../api/types'
import { Money } from '../format/Amount'
import { Icon } from '../ui/Icon'

function Change({ value }: { value: string | null | undefined }) {
  if (value === null || value === undefined) return <span className="change-empty">אין</span>
  const n = Number(value)
  return <Money value={value} signed className={n < 0 ? 'negative' : n > 0 ? 'positive' : undefined} />
}

function Rows({ rows }: { rows: ChangeRow[] }) {
  if (!rows.length) return null
  return (
    <ul className="change-values">
      {rows.map((r) => (
        <li key={r.field}>
          <span className="change-label">{r.label}</span>
          <span className="change-move">
            {r.before && <><s><bdi>{r.before}</bdi></s><Icon name="arrowLeft" size={14} /></>}
            <strong><bdi>{r.after}</bdi></strong>
          </span>
        </li>
      ))}
    </ul>
  )
}

function Options({ options }: { options: CardOption[] }) {
  if (!options.length) return null
  return (
    <table className="change-table">
      <thead>
        <tr><th scope="col">רכב</th><th scope="col">לפני</th><th scope="col">אחרי</th></tr>
      </thead>
      <tbody>
        {options.map((o) => (
          <tr key={o.option_id}>
            <th scope="row"><bdi>{o.label}</bdi></th>
            <td><Change value={o.before?.delta} /></td>
            <td><Change value={o.after.delta} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function TodayNet({ before, after }: { before: string | null; after: string | null }) {
  if (!before || !after || before === after) return null
  return (
    <p className="change-today">
      <span>הנטו שלכם ברכב של היום</span>
      <span className="change-move">
        <s><Money value={before} /></s><Icon name="arrowLeft" size={14} /><strong><Money value={after} /></strong>
      </span>
    </p>
  )
}

export function ChangeCard({ card }: { card: MessageCard }) {
  if (card.kind === 'scenario') {
    return (
      <section className="change-card is-check" aria-label="בדיקה בלבד">
        <p className="change-title"><Icon name="eye" size={16} />בדיקה בלבד: הנתונים שלכם לא השתנו</p>
        <Rows rows={card.values} />
        <TodayNet before={card.today_net.before} after={card.today_net.after} />
        <Options options={card.options} />
        {[...card.left, ...card.not_eligible].map((o) => (
          <p key={o.option_id} className="change-note"><bdi>{o.label}</bdi>: {o.reason}.</p>
        ))}
      </section>
    )
  }
  return (
    <section className="change-card" aria-label="מה השתנה">
      <p className="change-title"><Icon name="check" size={16} />מה השתנה</p>
      <Rows rows={card.changes} />
      <TodayNet before={card.today_net.before} after={card.today_net.after} />
      <Options options={card.options} />
      {card.options.length > 1 && (
        <p className="change-note">{card.order_changed ? 'הסדר בין הרכבים השתנה.' : 'הסדר בין הרכבים לא השתנה.'}</p>
      )}
      {card.removed.map((o) => (
        <p key={o.option_id} className="change-note is-removed"><bdi>{o.label}</bdi> יצא מההשוואה. {o.reason}.</p>
      ))}
      {card.left.map((o) => (
        <p key={o.option_id} className="change-note"><bdi>{o.label}</bdi> כבר לא בין המובילים.</p>
      ))}
    </section>
  )
}
