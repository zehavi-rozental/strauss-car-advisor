// Makes the PDF in the browser and saves it. The PDF code and the four font files are loaded once, as soon as a
// result that can be reported is on screen, so the download also works if the connection drops afterwards.
// The results never leave the browser: no request is made with them.
import hebrew400 from '@fontsource/assistant/files/assistant-hebrew-400-normal.woff?url'
import hebrew700 from '@fontsource/assistant/files/assistant-hebrew-700-normal.woff?url'
import latin400 from '@fontsource/assistant/files/assistant-latin-400-normal.woff?url'
import latin700 from '@fontsource/assistant/files/assistant-latin-700-normal.woff?url'
import type { OptionResult } from '../../api/types'
import { readReportTheme } from '../../art/reportTheme'
import type { ReportFonts } from './pdfReport'
import { buildReport, reportFileName } from './reportModel'

type Ready = [typeof import('./pdfReport'), ReportFonts]
let ready: Promise<Ready> | null = null

const fetchBytes = async (url: string) => {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`font ${r.status}`)
  return r.arrayBuffer()
}

/** Load the PDF code and the fonts (once; a failed load is tried again next time). */
export function prepareReport(): Promise<Ready> {
  ready ??= Promise.all([
    import('./pdfReport'),
    Promise.all([hebrew400, latin400, hebrew700, latin700].map(fetchBytes))
      .then(([hebrew, latin, hebrewBold, latinBold]) => ({ hebrew, latin, hebrewBold, latinBold })),
  ])
  ready.catch(() => { ready = null })
  return ready
}

/** Build the report from these results, as they are now, and hand the PDF to the browser to save. */
export async function downloadReport(results: OptionResult[], now = new Date()): Promise<void> {
  const [{ renderReportPdf }, fonts] = await prepareReport()
  const { bytes } = await renderReportPdf(buildReport(results, now), fonts, readReportTheme())
  const url = URL.createObjectURL(new Blob([bytes.slice().buffer], { type: 'application/pdf' }))
  const a = document.createElement('a')
  a.href = url
  a.download = reportFileName(now)
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
}
