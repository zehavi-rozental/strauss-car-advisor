// What the comparison report says, from the results already on screen (the server's ScenarioResult) and nothing
// else: no request, no new calculation, no other conversation. Every amount is a server value; the only arithmetic
// is display arithmetic that the screen already does (the annual figure = the rounded monthly × 12) and the gap
// between two options' rounded monthly nets, both shown in the report. A missing value stays missing (null),
// never 0.
import type { OptionResult } from '../../api/types'
import { annualFromMonthly } from '../../format/money'
import type { Row } from '../comparison'
import { STATE_LABEL, breakdownRows, rankResults, resultState, toneOf } from '../resultView'
import type { ResultState, Tone } from '../resultView'

export interface ReportToday {
  label: string
  net: string            // net_display
  copay: string
  useValue: string
}

export interface ReportOption {
  id: string
  label: string
  rank: number                  // 1 = the highest monthly change; options without a result come last
  state: ResultState
  stateLabel: string            // "הערכה זמנית" / "חישוב מלא" / ...
  calculated: boolean           // the engine returned a net for this option
  net: string | null            // alternative.net_display
  delta: string | null          // delta_display: the change against today
  annual: number | null         // the rounded monthly delta × 12 (as on the result card)
  tone: Tone
  copay: string | null
  copayPending: boolean
  useValue: string | null
  why: string | null
  reason: string | null         // why there is no result (not eligible / missing data)
  highest: boolean              // the highest estimated monthly net among the calculated options (ties: all)
  gapToHighest: number | null   // highest net − this net, in whole shekels; null for the highest or without a net
}

export interface ReportModel {
  generatedAt: Date
  today: ReportToday | null
  options: ReportOption[]
  /** the breakdown table, the same rows as the comparison screen, columns in the options' order */
  rows: Row[]
  highestCount: number
  assumptions: string[]
  pending: boolean              // some copay is still the catalog's, waiting for the representative
}

const calculatedNet = (r: OptionResult) =>
  r.result.status === 'OK' && r.result.alternative != null && r.result.delta_display != null ? r.result.alternative.net_display : null

/** The report is offered only for current results with at least one calculated option. */
export function canReport(results: OptionResult[], stale: boolean): boolean {
  return !stale && results.some((r) => calculatedNet(r) != null)
}

export function buildReport(results: OptionResult[], generatedAt: Date): ReportModel {
  const ranked = rankResults(results)            // every result, never cut to 3
  const nets = ranked.map((r) => calculatedNet(r)).filter((n): n is string => n != null).map((n) => Math.round(Number(n)))
  const top = nets.length ? Math.max(...nets) : null
  const todaySide = ranked.find((r) => r.result.today)?.result.today ?? null

  const options: ReportOption[] = ranked.map((item, i) => {
    const r = item.result
    const net = calculatedNet(item)
    const state = resultState(r)
    const rounded = net == null ? null : Math.round(Number(net))
    const highest = rounded != null && rounded === top
    return {
      id: item.option_id,
      label: item.label,
      rank: i + 1,
      state,
      stateLabel: STATE_LABEL[state],
      calculated: net != null,
      net,
      delta: net != null ? r.delta_display : null,
      annual: net != null && r.delta_display != null ? annualFromMonthly(r.delta_display) : null,
      tone: net != null ? toneOf(r.delta_display) : 'flat',
      copay: net != null ? r.alternative!.copay : null,
      copayPending: net != null && r.copay_pending,
      useValue: net != null ? r.alternative!.use_value : null,
      why: r.why || null,
      reason: r.reason || null,
      highest,
      gapToHighest: rounded != null && top != null && !highest ? top - rounded : null,
    }
  })

  return {
    generatedAt,
    today: todaySide
      ? { label: todaySide.label, net: todaySide.net_display, copay: todaySide.copay, useValue: todaySide.use_value }
      : null,
    options,
    rows: breakdownRows(ranked),
    highestCount: options.filter((o) => o.highest).length,
    assumptions: [...new Set(ranked.flatMap((r) => r.result.assumptions))],
    pending: options.some((o) => o.copayPending),
  }
}

/** "22.10.2026, 14:32" in Israel's way of writing it. */
export function generatedText(at: Date): string {
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(at.getDate())}.${two(at.getMonth() + 1)}.${at.getFullYear()}, ${two(at.getHours())}:${two(at.getMinutes())}`
}

/** "STRAUSS-DRIVE-comparison-2026-10-22-1432.pdf" (ASCII, safe on every phone and file system). */
export function reportFileName(at: Date): string {
  const two = (n: number) => String(n).padStart(2, '0')
  return `STRAUSS-DRIVE-comparison-${at.getFullYear()}-${two(at.getMonth() + 1)}-${two(at.getDate())}-${two(at.getHours())}${two(at.getMinutes())}.pdf`
}
