import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { CarActions, mapMissingText } from './CarActions'
import { BROWSE_NOTE, MOVE_TO_CHAT, canMoveToChat } from './ComparisonPanel'

// the names as the server sends them in the view (templates.CAR_ACTIONS, #10 27/11)
const LABELS = { choose: 'לבחור רכבים להשוואה', change: 'לשנות בחירה', rank: '3 המשתלמים ביותר', map: 'כל הרכבים לפי נטו' }
const { choose: CHOOSE_LABEL, change: CHANGE_LABEL, rank: RANK_LABEL, map: MAP_LABEL } = LABELS
const noop = () => {}
const actions = (p: Partial<Parameters<typeof CarActions>[0]>) => renderToStaticMarkup(
  <CarActions labels={LABELS} selected={[]} canChoose canRank mapReady mapExists={false} mapMissing={[]} disabled={false}
              onChoose={noop} onRank={noop} onMap={noop} {...p} />)
const count = (html: string, text: string) => html.split(text).length - 1
const text = (html: string) => html.replace(/&#x27;/g, "'")

describe('the cars, one compact place (1/11)', () => {
  it('the alternatives question: choose, the 3 highest, every car by net — once each', () => {
    const out = text(actions({}))
    for (const label of [CHOOSE_LABEL, RANK_LABEL, MAP_LABEL]) expect(count(out, label)).toBe(1)
    expect(out).not.toContain(CHANGE_LABEL)
    expect(out).not.toContain("בהשוואה בצ'אט")
  })

  it("after results: the chat's comparison is named, and choosing says it changes it", () => {
    const out = text(actions({ selected: ['NIRO (2025)', 'BYD DOLPHIN (2025)'] }))
    expect(out.replace(/<[^>]+>/g, '')).toContain("בהשוואה בצ'אט: NIRO (2025), BYD DOLPHIN (2025)")
    // each name is one unit that never breaks inside (9/11: "BYD" ended a line, "DOLPHIN (2025)" began the next)
    expect(out).toContain('<bdi class="car-name">BYD DOLPHIN (2025)</bdi>')
    expect(out).toContain(CHANGE_LABEL)
    expect(out).not.toContain(CHOOSE_LABEL)
  })

  it('without the alternatives question only the map is offered (nothing to choose into)', () => {
    const out = actions({ canChoose: false })
    expect(out).not.toContain(CHOOSE_LABEL)
    expect(out).not.toContain(RANK_LABEL)
    expect(out).toContain(MAP_LABEL)
  })

  it("the buttons' names are the server's, never the component's own (one source with the chat's text, #10)", () => {
    const named = { choose: 'א-בחירה', change: 'ב-שינוי', rank: 'ג-שלושה', map: 'ד-מפה' }
    const before = text(actions({ labels: named }))
    for (const label of [named.choose, named.rank, named.map]) expect(count(before, label)).toBe(1)
    expect(text(actions({ labels: named, selected: ['NIRO (2025)'] }))).toContain(named.change)
    for (const label of Object.values(LABELS)) expect(before).not.toContain(label)
  })

  it('the map not ready: the entry stays, and says what is missing and how to give it', () => {
    expect(actions({ mapReady: false, mapMissing: ['פנסיה'] })).toContain(MAP_LABEL)
    expect(mapMissingText(['פנסיה', 'נקודות זיכוי'])).toBe("כדי לדרג את כל הרכבים חסרים לי עוד: פנסיה, נקודות זיכוי. אפשר לכתוב אותם כאן בצ'אט")
    expect(mapMissingText([])).toContain('אחרי תשובת נציג התחבורה')
  })
})

describe('the comparison from the map is for looking (1/11)', () => {
  const options = { choices: [{ id: 'CAR-03' }, { id: 'CAR-05' }] }
  it('moves into the chat only on an explicit button, and only cars the chat offers', () => {
    expect(BROWSE_NOTE).toContain("ההשוואה בצ'אט לא משתנה")
    expect(MOVE_TO_CHAT).toBe("להעביר להשוואה בצ'אט")
    expect(canMoveToChat(['CAR-03', 'CAR-05'], options, false)).toBe(true)
    expect(canMoveToChat(['CAR-03', 'CAR-09'], options, false)).toBe(false)   // not offered: no button
    expect(canMoveToChat(['CAR-03'], null, false)).toBe(false)                  // no options question now
    expect(canMoveToChat(['CAR-03'], options, true)).toBe(false)                // out of date: never moved
  })
})
