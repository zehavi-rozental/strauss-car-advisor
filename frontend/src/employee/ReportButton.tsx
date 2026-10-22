// "הורדת דוח השוואה": the comparison on screen as a PDF, made in the browser (report/). Shown only for current
// results with at least one calculated option; an out-of-date result is never reported.
import { useEffect, useState } from 'react'
import type { OptionResult } from '../api/types'
import { Icon } from '../ui/Icon'
import { downloadReport, prepareReport } from './report/download'
import { canReport } from './report/reportModel'

export function ReportButton({ results, stale = false }: { results: OptionResult[]; stale?: boolean }) {
  const [state, setState] = useState<'idle' | 'busy' | 'error'>('idle')
  const offered = canReport(results, stale)
  useEffect(() => {
    if (offered) prepareReport().catch(() => undefined)      // ready before the click, and offline after it
  }, [offered])
  if (!offered) return null

  const download = async () => {
    setState('busy')
    try {
      await downloadReport(results)
      setState('idle')
    } catch {
      setState('error')
    }
  }
  return (
    <span className="report-action">
      <button type="button" className="table-button report-button" disabled={state === 'busy'} onClick={download}>
        <Icon name="download" size={18} />{state === 'busy' ? 'מכינים את הדוח…' : 'הורדת דוח השוואה'}
      </button>
      {state === 'error' && <span className="report-error" role="alert">לא הצלחנו להכין את הדוח. נסו שוב.</span>}
    </span>
  )
}
