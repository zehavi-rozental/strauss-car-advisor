// Right-to-left text for the PDF report. A PDF draws glyphs from left to right, so each line is put in visual
// order with the Unicode bidi algorithm (bidi-js): Hebrew reads from the right, car names and numbers keep their
// own order, and amounts are isolated left to right exactly like the <bdi> amounts on screen.
// One catch: fontkit (inside pdf-lib) lays out a run that contains Hebrew letters right to left by itself, so such
// a run is handed over reversed (see `glyphOrder`).
import bidiFactory from 'bidi-js'

const bidi = bidiFactory()

const LRI = '⁦'
const PDI = '⁩'
const CONTROLS = /[⁦-⁩‎‏‪-‮]/g
const HEBREW_LETTER = /[֐-׿יִ-ﭏ]/
/** Characters the Hebrew half of the font carries (the rest is in the Latin half). */
const HEBREW_FONT = /[֐-׿יִ-ﭏ₪]/

/** An amount or any left-to-right piece inside Hebrew text, isolated like <bdi>. */
export const ltr = (text: string) => `${LRI}${text}${PDI}`

/** The line as it appears on the page, left to right, mirrored brackets included, bidi controls removed. */
export function visualOrder(text: string, direction: 'rtl' | 'ltr' = 'rtl'): string {
  const levels = bidi.getEmbeddingLevels(text, direction)
  const mirrored = bidi.getMirroredCharactersMap(text, levels.levels)
  const chars = Array.from(text, (c, i) => mirrored.get(i) ?? c)
  for (const [start, end] of bidi.getReorderSegments(text, levels)) {
    const part = chars.slice(start, end + 1).reverse()
    chars.splice(start, end - start + 1, ...part)
  }
  return chars.join('').replace(CONTROLS, '')
}

export interface Run {
  text: string           // in visual order
  hebrew: boolean        // drawn with the Hebrew half of the font
}

/** A visual line split into runs that each use one half of the font. */
export function fontRuns(visual: string): Run[] {
  const runs: Run[] = []
  for (const c of visual) {
    const hebrew = HEBREW_FONT.test(c)
    const last = runs[runs.length - 1]
    if (last && last.hebrew === hebrew) last.text += c
    else runs.push({ text: c, hebrew })
  }
  return runs
}

/** The characters to hand to pdf-lib so that the glyphs land in visual order (fontkit reverses Hebrew runs). */
export const glyphOrder = (run: Run) => (HEBREW_LETTER.test(run.text) ? Array.from(run.text).reverse().join('') : run.text)

/** Without bidi controls: what the line says, for tests and the PDF's metadata. */
export const plain = (text: string) => text.replace(CONTROLS, '')
