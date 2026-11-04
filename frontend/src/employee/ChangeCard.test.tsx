import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import type { Facts, MessageCard, OptionResult } from '../api/types'
import { CarActions } from './CarActions'
import { ChangeCard } from './ChangeCard'
import { ContextStrip, PrivacyLines, PrivacyNotice, ResumeCard } from './DataSheet'
import { MessageList, SUPERSEDED_LABEL, UNUSUAL_NOTE } from './MessageList'

const option = (id: string, label: string, before: string | null, after: string) => ({
  option_id: id, label, before: before === null ? null : { net: '1', delta: before }, after: { net: '2', delta: after },
})
const receipt: MessageCard = {
  kind: 'receipt', changes: [{ field: 'base_salary', label: 'שכר בסיס', before: '18,000 ₪', after: '20,000 ₪' }],
  today_net: { before: '10703', after: '11602' }, options: [option('CAR-06', 'MG4 (2025)', '979', '1047'), option('CAR-03', 'DOLPHIN', '960', '1022')],
  removed: [{ option_id: 'CAR-19', label: 'CARNIVAL (2025)', reason: 'מסלול משפחתי: מ-4 ילדים עד גיל 23' }], added: [], left: [],
  order_changed: false, ranked: 12,
}
const check: MessageCard = {
  kind: 'scenario', values: receipt.changes, today_net: receipt.today_net, options: receipt.options, left: [], not_eligible: [],
  order_changed: false, apply: { values: { base_salary: '20000' } },
}

describe('what a change did, and a check', () => {
  it('the receipt shows before and after, the order and why a car left, all from the server', () => {
    const html = renderToStaticMarkup(<ChangeCard card={receipt} />)
    for (const text of ['מה השתנה', '18,000 ₪', '20,000 ₪', 'MG4 (2025)', 'הסדר בין הרכבים לא השתנה', 'CARNIVAL (2025)', 'יצא מההשוואה', 'מסלול משפחתי']) {
      expect(html).toContain(text)
    }
    expect(html).toContain('10,703')
    expect(html).toContain('11,602')
  })

  it('a check says first that nothing changed', () => {
    const html = renderToStaticMarkup(<ChangeCard card={check} />)
    expect(html.indexOf('בדיקה בלבד')).toBeLessThan(html.indexOf('20,000 ₪'))
    expect(html).toContain('הנתונים שלכם לא השתנו')
    expect(html).not.toContain('מה השתנה')
  })
})

const facts: Facts = {
  version: 3, results_version: 2, ranked: true, missing: ['בונוס'], assumptions: [], strip: ['base_salary'],
  items: [{ key: 'base_salary', label: 'שכר בסיס', section: 'pay', value: '18000', display: '18,000 ₪', source: 'chat', source_label: 'מהשיחה',
            status: 'known', status_label: '', edit: 'money', used_in_calc: true, note: '' }],
}

describe('what is known, in the chat', () => {
  it('the strip names what this stage is about and what is missing, each one a button', () => {
    const html = renderToStaticMarkup(<ContextStrip facts={facts} onOpen={() => {}} />)
    expect(html).toContain('הנתונים שלי')
    expect(html).toContain('1 חסרים')
    expect(html).toContain('18,000 ₪')
    expect(html).toContain('aria-label="שכר בסיס: 18,000 ₪. לעדכון"')
  })

  it('the strip draws nothing before the server said anything', () => {
    expect(renderToStaticMarkup(<ContextStrip facts={null} onOpen={() => {}} />)).toBe('')
  })
})

const privacy = { ai: { available: true, required: true, accepted: false, version: 'v' }, payslip: { accepted: false, version: 'v' },
                  retention_days: 30, idle_minutes: 120, demo: true }

describe('privacy on the screen', () => {
  it('the notice says where the data goes, that this is a demo, and that the conversation works without the model', () => {
    const html = renderToStaticMarkup(<PrivacyNotice privacy={privacy} disabled={false} onAccept={() => {}} onHide={() => {}} />)
    for (const text of ['Google Gemini', 'הדגמה', 'אל תזינו', 'בלי אישור', 'להמשיך בלי AI']) expect(html).toContain(text)
    expect(html).not.toContain('checked')
  })

  it('the periods are the server\'s, and the limits of deletion are said', () => {
    const html = renderToStaticMarkup(<PrivacyLines privacy={privacy} />)
    for (const text of ['30 ימים', '120 דקות', 'עותקי גיבוי']) expect(html).toContain(text)
  })

  it('a saved conversation shows nothing of itself until someone chooses it', () => {
    const open = renderToStaticMarkup(<ResumeCard locked={false} busy={false} error={null} onResume={() => {}} onDelete={() => {}} />)
    expect(open).toContain('להמשיך את השיחה')
    expect(open).toContain('למחוק')
    const locked = renderToStaticMarkup(<ResumeCard locked busy={false} error={null} onResume={() => {}} onDelete={() => {}} />)
    expect(locked).toContain('ננעלה')
    expect(locked).not.toContain('להמשיך את השיחה')
  })
})

describe('the three ways to see the cars', () => {
  const html = (active: 'chosen' | 'top3' | 'all' | null) => renderToStaticMarkup(
    <CarActions selected={['MG4']} canChoose canRank mapReady mapExists={false} mapMissing={[]} disabled={false}
                onChoose={() => {}} onRank={() => {}} onMap={() => {}} active={active} />)

  it('one group of three buttons, exactly one pressed', () => {
    const out = html('top3')
    expect(out).toContain('role="group"')
    expect(out.split('aria-pressed="true"').length - 1).toBe(1)
    expect(out.split('aria-pressed="false"').length - 1).toBe(2)
  })

  it('nothing pressed before there are results', () => {
    expect(html(null)).not.toContain('aria-pressed="true"')
  })
})

describe('older figures and amounts that stand out', () => {
  const message = (seq: number, version: number | null) => ({
    seq, role: 'assistant' as const, source: 'template', content: `הודעה ${seq}`, created_at: '2026-11-04T10:00:00Z', results_version: version,
  })
  const list = (results: OptionResult[], version: number) => renderToStaticMarkup(
    <MessageList messages={[message(1, 1), message(2, 2), message(3, null)]} results={results} resultsAnchor={2} typing={false} resultsVersion={version} />)

  it('a message that reported older results is marked, the current one and a plain one are not', () => {
    const html = list([], 2)
    expect(html.split(SUPERSEDED_LABEL).length - 1).toBe(1)
    expect(html.indexOf(SUPERSEDED_LABEL)).toBeLessThan(html.indexOf('הודעה 2'))
  })

  it('an unusual amount is marked, and its calculation stays on screen', () => {
    const unusual = { option_id: 'CAR-05', label: 'NIRO', run_id: 'r', result: { status: 'OK', unusual_copay: true, delta_display: '-349245', drivers: [],
      alternative: { net_display: '-338542' } } } as unknown as OptionResult
    const html = list([unusual], 2)
    expect(html).toContain(UNUSUAL_NOTE)
    expect(html).toContain('338')
  })
})
