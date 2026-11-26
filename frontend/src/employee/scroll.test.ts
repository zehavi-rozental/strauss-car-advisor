import { describe, expect, it } from 'vitest'
import { scrollTarget } from './scroll'

const base = { bot: true, typing: false, carriesCards: false, height: 120, visible: 600, shownTop: false }

describe('scrollTarget (#8, 24/11)', () => {
  it('a short reply keeps the old behaviour: the end of the chat', () => {
    expect(scrollTarget(base)).toBe('end')
  })

  it('a reply with the result cards is shown from its top (the best car first)', () => {
    expect(scrollTarget({ ...base, carriesCards: true })).toBe('top')
  })

  it('a reply taller than most of the visible area is shown from its top', () => {
    expect(scrollTarget({ ...base, height: 500 })).toBe('top')
    expect(scrollTarget({ ...base, height: 400 })).toBe('end')
  })

  it('once shown from its top, later updates leave the employee where they are', () => {
    expect(scrollTarget({ ...base, carriesCards: true, shownTop: true })).toBe('stay')
  })

  it("the employee's own message and the typing dots go to the end", () => {
    expect(scrollTarget({ ...base, bot: false, carriesCards: true })).toBe('end')
    expect(scrollTarget({ ...base, typing: true, carriesCards: true })).toBe('end')
  })
})
