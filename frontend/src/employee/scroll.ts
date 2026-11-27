// Where the chat goes when it changes (#8, 24/11; #11, 27/11). A new reply from the bot that carries tall content
// (the result cards, "3 המשתלמים ביותר", the option map's card) or is taller than most of the visible area is shown
// from its TOP: the first line of the bot's reply bubble, below anything sticky, never the employee's own message
// above it ("הכי משתלם"). A short reply, the employee's own message and the typing dots keep the old behaviour (the
// end of the chat). Once a reply was brought to its top, later updates (the question's buttons appearing under it,
// a poll, an anchor that arrives a render later) leave the employee where they are.

export type ScrollTarget = 'top' | 'end' | 'stay'

export interface ScrollInput {
  /** the bot answered the employee's latest message (the reply is the newest thing, not the employee's message) */
  bot: boolean
  /** the bot is typing: the dots are the newest thing */
  typing: boolean
  /** the reply carries the result cards or the option map's card */
  carriesCards: boolean
  /** the reply's height (all its messages, with their cards) and the chat's visible height, in px */
  height: number
  visible: number
  /** this reply was already brought to its top */
  shownTop: boolean
}

/** A reply taller than this share of the visible area is read from its top. */
export const TALL_SHARE = 0.7
/** Space left above the reply's first line, in px. */
export const TOP_GAP = 8

export function scrollTarget(i: ScrollInput): ScrollTarget {
  if (i.typing || !i.bot) return 'end'
  const tall = i.carriesCards || (i.visible > 0 && i.height > i.visible * TALL_SHARE)
  if (!tall) return 'end'
  return i.shownTop ? 'stay' : 'top'
}

/** The index of the first message of the bot's reply to the employee's latest message (a reply may be several
 *  messages: an acknowledgement, the results); -1 while the employee's message is the newest. */
export function replyStart(messages: readonly { role: string }[]): number {
  let i = messages.length - 1
  if (i < 0 || messages[i].role === 'user') return -1
  while (i >= 0 && messages[i].role !== 'user') i--
  return i + 1
}

/** The scroll position of the chat that puts the reply's first line at the top of the visible area, below
 *  whatever sticky element covers that edge, with a small gap. */
export function topScroll(p: { scrollTop: number; viewTop: number; targetTop: number; cover: number }): number {
  return Math.max(0, Math.round(p.scrollTop + (p.targetTop - p.viewTop) - p.cover - TOP_GAP))
}

function stuck(el: Element | null, stop: Element): HTMLElement | null {
  for (let e = el; e && e !== stop && e !== document.documentElement; e = e.parentElement) {
    const position = getComputedStyle(e).position
    if (position === 'sticky' || position === 'fixed') return e as HTMLElement
  }
  return null
}

/** How many px at the top of the chat's visible area are covered by a sticky or fixed element (a header, a bar
 *  such as "עריכת הנתונים שלי" if it ever sticks there): the reply's first line goes below it. */
export function stickyCover(view: HTMLElement): number {
  if (typeof document.elementsFromPoint !== 'function') return 0
  const r = view.getBoundingClientRect()
  const top = Math.max(r.top, 0)
  let cover = 0
  for (const x of [r.left + 24, r.left + r.width / 2, r.right - 24]) {
    for (const el of document.elementsFromPoint(x, top + 1)) {
      const s = stuck(el, view)
      if (s && !s.contains(view)) cover = Math.max(cover, s.getBoundingClientRect().bottom - top)
    }
  }
  return Math.max(0, cover)
}

/** An element's top as laid out (offsetTop up the chain): neither the chat's scroll nor a transform moves it, so a
 *  reply that is still rising in (translateY) is measured where it will stand. */
function layoutTop(el: HTMLElement): number {
  let top = 0
  for (let e: HTMLElement | null = el; e; e = e.offsetParent as HTMLElement | null) top += e.offsetTop
  return top
}

/** Bring the reply's first line to the top of the chat's visible area (below anything sticky). */
export function scrollToReply(view: HTMLElement | null, target: HTMLElement, behavior: ScrollBehavior): void {
  if (!view) {
    target.scrollIntoView({ behavior, block: 'start' })
    return
  }
  const viewTop = view.getBoundingClientRect().top
  // where the target stands in the visible area once laid out (the scroll and the entrance animation taken out)
  const targetTop = viewTop + layoutTop(target) - layoutTop(view) - view.clientTop - view.scrollTop
  view.scrollTo({ top: topScroll({ scrollTop: view.scrollTop, viewTop, targetTop, cover: stickyCover(view) }), behavior })
}
