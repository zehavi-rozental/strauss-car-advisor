import { describe, expect, it } from 'vitest'
import { TOP_GAP, replyStart, scrollTarget, topScroll } from './scroll'

const msg = (role: string) => ({ role })

describe("the bot's reply, not the employee's message (#11, 27/11)", () => {
  it('the reply starts after the employee\'s latest message, even when it is several messages', () => {
    expect(replyStart([msg('assistant'), msg('user'), msg('assistant')])).toBe(2)
    expect(replyStart([msg('assistant'), msg('user'), msg('assistant'), msg('assistant')])).toBe(2)
    expect(replyStart([msg('assistant')])).toBe(0)                    // the greeting
  })

  it("while the employee's message is the newest there is no reply yet", () => {
    expect(replyStart([msg('assistant'), msg('user')])).toBe(-1)
    expect(replyStart([])).toBe(-1)
  })

  it("puts the reply's first line at the top of the visible area, below anything sticky", () => {
    // the bubble is 300px below the chat's top edge, the chat is scrolled 1000px: scroll by 300 (less the gap)
    expect(topScroll({ scrollTop: 1000, viewTop: 100, targetTop: 400, cover: 0 })).toBe(1300 - TOP_GAP)
    // a 48px sticky bar over the top edge: the first line goes below it, not under it
    expect(topScroll({ scrollTop: 1000, viewTop: 100, targetTop: 400, cover: 48 })).toBe(1300 - 48 - TOP_GAP)
    expect(topScroll({ scrollTop: 0, viewTop: 100, targetTop: 102, cover: 0 })).toBe(0)     // never above the start
  })
})

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
