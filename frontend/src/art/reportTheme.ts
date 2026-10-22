// The PDF report's colours. In the browser they are read from the design tokens (styles/tokens.css), so the report
// follows any change of style with no code change. The fallback below is only for a run without a page (the unit
// tests in Node); it mirrors the tokens and is not a second source of truth.
import { toRGB } from './palette'
import type { RGB } from './palette'

export const REPORT_COLOURS = {
  hero: '--hero', heroDeep: '--hero-deep', onHero: '--on-hero', onHero2: '--on-hero-2', onHero3: '--on-hero-3',
  onHeroLive: '--on-hero-live', onHeroDown: '--on-hero-down',
  ink: '--ink', ink2: '--ink-2', ink3: '--ink-3', line: '--line', paper: '--paper', card: '--card', card2: '--card-2',
  brand: '--brand', up: '--up', down: '--down', pending: '--pending', pendingTint: '--pending-tint',
  surfaceToday: '--surface-today', surfaceUp: '--surface-up', surfaceDown: '--surface-down', skyTint: '--sky-tint',
  red: '--petal-red', orange: '--petal-orange', leaf: '--petal-leaf', green: '--petal-green', sky: '--petal-sky',
  turq: '--petal-turq', blue: '--petal-blue',
} as const

export type ReportColour = keyof typeof REPORT_COLOURS
export type ReportTheme = Record<ReportColour, RGB>

const FALLBACK: Record<ReportColour, string> = {
  hero: '#073d5f', heroDeep: '#06304d', onHero: '#ffffff', onHero2: '#d6eaf6', onHero3: '#a9cfe6',
  onHeroLive: '#c9f1a0', onHeroDown: '#ffc4ba',
  ink: '#1b2430', ink2: '#48525e', ink3: '#5e6873', line: '#e6e9ed', paper: '#f0f3f6', card: '#ffffff', card2: '#f3f5f8',
  brand: '#0b72a8', up: '#3e6a10', down: '#be2419', pending: '#9a4705', pendingTint: '#fef2e6',
  surfaceToday: '#fdebdc', surfaceUp: '#eff6e4', surfaceDown: '#fdecea', skyTint: '#eef8fc',
  red: '#ed3024', orange: '#f48326', leaf: '#b5d36a', green: '#78a12f', sky: '#9ed8ef', turq: '#00bae2', blue: '#0390cb',
}

const parseHex = (h: string): RGB => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB

export const DEFAULT_REPORT_THEME: ReportTheme =
  Object.fromEntries(Object.entries(FALLBACK).map(([k, v]) => [k, parseHex(v)])) as ReportTheme

/** The tokens as they are on the page now (a surface token may itself be a var() or a color-mix()). */
export function readReportTheme(): ReportTheme {
  if (typeof document === 'undefined') return DEFAULT_REPORT_THEME
  const style = getComputedStyle(document.documentElement)
  return Object.fromEntries(Object.entries(REPORT_COLOURS).map(([key, token]) => [
    key, toRGB(style.getPropertyValue(token), DEFAULT_REPORT_THEME[key as ReportColour]),
  ])) as ReportTheme
}
