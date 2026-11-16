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

describe('the address is optional (1/11)', () => {
  it('offered, marked optional, never required', () => {
    const out = card({ emailEnabled: true })
    expect(out).toContain('(לא חובה)')
    expect(out).not.toContain('required')
  })
})

describe('the address field appears every time it is needed (10/11)', () => {
  const props = (p: { emailSimulated?: boolean; emailPending?: boolean; knownEmail?: string }) => renderToStaticMarkup(
    <RepInquiryCard conversationId="c1" initialQuestion="באיזה צבעים יש את NIRO?" intent="general" emailEnabled
                    onCancel={() => {}} onSaved={() => {}} {...p} />)

  it('simulated mail (a test server) still asks the address, and says no real email is sent', () => {
    const out = props({ emailSimulated: true })
    expect(out).toContain('type="email"')
    expect(out).toContain('לא נשלח מייל אמיתי')
  })

  it('an address waiting for its verification: the field stays, and another address may be typed', () => {
    const out = props({ emailPending: true })
    expect(out).toContain('type="email"')
    expect(out).toContain('ממתינה לאימות')
  })

  it('a verified address is named with a way to change it', () => {
    const out = props({ knownEmail: 'dana@example.com' })
    expect(out).toContain('dana@example.com')
    expect(out).toContain('שינוי כתובת')
  })
})
