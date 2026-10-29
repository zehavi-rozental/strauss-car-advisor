import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { RichText } from './richText'

const html = (text: string) => renderToStaticMarkup(<RichText text={text} />)

describe('RichText', () => {
  it('keeps line breaks and bold', () => {
    expect(html('שלום **עולם**\nשורה')).toContain('שלום <strong>עולם</strong></span><span><br/>שורה')
  })

  it('renders bullets', () => {
    expect(html('- אחד\n- שניים')).toContain('<ul><li>אחד</li><li>שניים</li></ul>')
  })

  it('wraps every amount in bdi and uses one minus sign', () => {
    const out = html('יקטן ב-923 ₪, והיה -50 ₪ ו-1,200 ₪')
    expect(out).toContain('<bdi class="money amt">923 ₪</bdi>')
    expect(out).toContain('<bdi class="money amt">−50 ₪</bdi>')
    expect(out).toContain('<bdi class="money amt">1,200 ₪</bdi>')
  })

  it('keeps a Latin model name whole and left-to-right, with its year', () => {
    const out = html('ב-BYD DOLPHIN (2025) ההשתתפות היא 200 ₪, וב-MG4 פחות')
    expect(out).toContain('<bdi dir="ltr" class="latin">BYD DOLPHIN (2025)</bdi>')
    expect(out).toContain('<bdi dir="ltr" class="latin">MG4</bdi>')
    expect(out).toContain('<bdi class="money amt">200 ₪</bdi>')
    expect(html('כתבו ל-name@example.com')).not.toContain('class="latin"')   // lower case is left as it is
    // the Hebrew prefix travels with the name, in one unit
    expect(out).toContain('<span class="latin-run">ב-<bdi dir="ltr" class="latin">BYD DOLPHIN (2025)</bdi></span>')
    expect(out).toContain('<span class="latin-run">וב-<bdi dir="ltr" class="latin">MG4</bdi></span>')
  })

  it('never renders raw HTML', () => {
    const out = html('<img src=x onerror=alert(1)> **<b>x</b>**')
    expect(out).not.toContain('<img')
    expect(out).toContain('&lt;img')
  })
})
