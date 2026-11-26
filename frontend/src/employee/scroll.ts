// Where the chat goes when it changes (#8, 24/11). A new reply from the bot that carries tall content (the result
// cards, "3 המשתלמים ביותר", the option map's card) or is taller than most of the visible area is shown from its TOP,
// so the best car comes first; a short reply, the employee's own message and the typing dots keep the old behaviour
// (the end of the chat). Once a reply was brought to its top, later updates (the question's buttons appearing under
// it, a poll) leave the employee where they are.

export type ScrollTarget = 'top' | 'end' | 'stay'

export interface ScrollInput {
  /** the latest message is the bot's (not the employee's) */
  bot: boolean
  /** the bot is typing: the dots are the newest thing */
  typing: boolean
  /** the latest message carries the result cards or the option map's card */
  carriesCards: boolean
  /** the latest message's height and the chat's visible height, in px */
  height: number
  visible: number
  /** this message was already brought to its top */
  shownTop: boolean
}

/** A reply taller than this share of the visible area is read from its top. */
export const TALL_SHARE = 0.7

export function scrollTarget(i: ScrollInput): ScrollTarget {
  if (i.typing || !i.bot) return 'end'
  const tall = i.carriesCards || (i.visible > 0 && i.height > i.visible * TALL_SHARE)
  if (!tall) return 'end'
  return i.shownTop ? 'stay' : 'top'
}
