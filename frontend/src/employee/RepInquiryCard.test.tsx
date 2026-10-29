import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { RepInquiryCard } from './RepInquiryCard'

const card = (props: { emailEnabled?: boolean; knownEmail?: string }) => renderToStaticMarkup(
  <RepInquiryCard conversationId="c1" initialQuestion="באיזה צבעים יש את NIRO?" intent="general"
                  onCancel={() => {}} onSaved={() => {}} {...props} />)

describe('RepInquiryCard email', () => {
  it('mail delivery off: no address is asked and nothing mentions email', () => {
    const out = card({ emailEnabled: false })
    expect(out).not.toContain('type="email"')
    expect(out).not.toContain('אימייל')
    expect(out).not.toContain('אימות')
  })

  it('mail on and no verified address: the address is asked, with the verification explained', () => {
    const out = card({ emailEnabled: true })
    expect(out).toContain('type="email"')
    expect(out).toContain('קישור חד־פעמי לאימות')
  })

  it('an address already verified for this conversation is not asked again', () => {
    expect(card({ emailEnabled: true, knownEmail: 'employee@example.com' })).not.toContain('type="email"')
  })
})
