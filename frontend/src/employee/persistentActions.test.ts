import { describe, expect, it } from 'vitest'
import type { Expects } from '../api/types'
import { persistentActions } from './widgets'

// The persistent actions (19/11): drawn only from the server's expects.actions, never from words in a reply.
const expects = (extra: Partial<Expects>) => ({
  field: null, type: 'none', choices: [], quick_replies: [], max: null, fields: [], ...extra,
}) as unknown as Expects

describe('persistent actions', () => {
  const upload = { kind: 'payslip_upload' as const, label: 'להעלות תלוש' }

  it('shows exactly what the server offers', () => {
    expect(persistentActions(expects({ actions: [upload] }), null)).toEqual([upload])
  })

  it('shows nothing the server did not offer', () => {
    expect(persistentActions(expects({}), null)).toEqual([])
    expect(persistentActions(expects({ actions: [] }), null)).toEqual([])
    expect(persistentActions(null, null)).toEqual([])
  })

  it('is not drawn twice next to the reply link for the same action, nor while it is open', () => {
    const withLink = expects({ actions: [upload], link: { kind: 'payslip_upload', label: 'להעלות תלוש' } })
    expect(persistentActions(withLink, null)).toEqual([])
    expect(persistentActions(expects({ actions: [upload] }), 'payslip_upload')).toEqual([])
  })
})
