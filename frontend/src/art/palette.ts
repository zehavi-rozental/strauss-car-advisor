// The artwork's colours come from the design tokens (styles/tokens.css, the --art-* section), so a new visual
// style is a change in the CSS only. This file just reads them; it has no colour of its own.

export type RGB = [number, number, number]

let probe: CanvasRenderingContext2D | null = null

/** Any CSS colour ("#ed3024", "rgb(…)", a named colour) as [r, g, b]. */
export function toRGB(value: string, fallback: RGB = [0, 0, 0]): RGB {
  const v = value.trim()
  if (!v) return fallback
  probe ??= document.createElement('canvas').getContext('2d')
  if (!probe) return fallback
  probe.fillStyle = '#000'
  probe.fillStyle = v
  const s = String(probe.fillStyle)
  if (s.startsWith('#')) return [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16)) as RGB
  const m = s.match(/[\d.]+/g)
  return m && m.length >= 3 ? [Number(m[0]), Number(m[1]), Number(m[2])] : fallback
}

export const rgba = (c: RGB, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`
export const hex = (c: RGB) => rgba(c, 1)

export interface ArtPalette {
  ribbons: RGB[]          // the trail, top ribbon to bottom ribbon
  paint: RGB[]            // the body, roof to sill (six stops)
  glass: RGB[]            // the side glass (five stops, the third is the sky across it)
  line: RGB               // panel lines and the outline
  pillar: RGB
  tyre: RGB
  rim: RGB[]              // three stops, light to dark
  rimInner: RGB
  spoke: RGB
  hub: RGB
  hubDot: RGB
  headLight: RGB
  tailLight: RGB
  shadow: RGB             // floor shadows
  reflection: RGB[]       // two stops
  skyTint: RGB            // the floor's colour picked up low on the doors
  highlight: RGB          // studio light: roof line, light box, the shimmer
}

/** Reads the --art-* tokens from the element's computed style. */
export function readPalette(el: Element): ArtPalette {
  const cs = getComputedStyle(el)
  const get = (name: string) => toRGB(cs.getPropertyValue(name))
  const list = (prefix: string, n: number) => Array.from({ length: n }, (_, i) => get(`${prefix}-${i + 1}`))
  return {
    ribbons: list('--art-ribbon', 7),
    paint: list('--art-paint', 6),
    glass: list('--art-glass', 5),
    line: get('--art-line'),
    pillar: get('--art-pillar'),
    tyre: get('--art-tyre'),
    rim: list('--art-rim', 3),
    rimInner: get('--art-rim-inner'),
    spoke: get('--art-spoke'),
    hub: get('--art-hub'),
    hubDot: get('--art-hub-dot'),
    headLight: get('--art-head-light'),
    tailLight: get('--art-tail-light'),
    shadow: get('--art-shadow'),
    reflection: list('--art-reflection', 2),
    skyTint: get('--art-sky-tint'),
    highlight: get('--art-highlight'),
  }
}
