import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ApiError } from '../api/client'
import { EmailReturnCard } from './EmailReturnCard'
import { returnLinkRetryable } from './useConversation'

const card = (props: { error?: string | null; invalid?: boolean; canReturn?: boolean }) => renderToStaticMarkup(
  <EmailReturnCard error={props.error ?? null} invalid={props.invalid ?? false} canReturn={props.canReturn ?? false}
                   sending={false} onOpen={() => {}} onNew={() => {}} />)

describe('returnLinkRetryable', () => {
  it('a used or expired link (410) is never tried again with the same token', () => {
    expect(returnLinkRetryable(new ApiError(410, 'הקישור אינו תקף'))).toBe(false)
  })

  it('other refusals would refuse the same token again', () => {
    for (const status of [400, 403, 404, 422]) expect(returnLinkRetryable(new ApiError(status, 'x'))).toBe(false)
  })

  it('no connection, too many requests and server errors may be tried again', () => {
    for (const status of [0, 429, 500, 502, 503]) expect(returnLinkRetryable(new ApiError(status, 'x'))).toBe(true)
    expect(returnLinkRetryable(new Error('unexpected'))).toBe(true)
  })
})

describe('EmailReturnCard', () => {
  it('a fresh link: the confirm button', () => {
    const out = card({})
    expect(out).toContain('פתיחת השיחה המאובטחת')
    expect(out).not.toContain('חזרה לשיחה')
  })

  it('a retryable failure keeps the confirm button, with the error', () => {
    const out = card({ error: 'אין חיבור לשרת' })
    expect(out).toContain('פתיחת השיחה המאובטחת')
    expect(out).toContain('אין חיבור לשרת')
  })

  it('after a 410 with a conversation stored here: no confirm button, only "חזרה לשיחה"', () => {
    const out = card({ error: 'הקישור אינו תקף או שכבר נוצל.', invalid: true, canReturn: true })
    expect(out).not.toContain('פתיחת השיחה המאובטחת')
    expect(out).toContain('חזרה לשיחה')
    expect(out).toContain('הקישור אינו תקף או שכבר נוצל.')
    expect(out).not.toContain('שיחה חדשה')
  })

  it('after a 410 with nothing stored here: no way back to any conversation, only a new one', () => {
    const out = card({ error: 'הקישור אינו תקף או שכבר נוצל.', invalid: true, canReturn: false })
    expect(out).not.toContain('פתיחת השיחה המאובטחת')
    expect(out).not.toContain('חזרה לשיחה')
    expect(out).toContain('שיחה חדשה')
  })
})
