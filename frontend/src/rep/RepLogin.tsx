import { useState } from 'react'
import type { FormEvent } from 'react'
import { FlowCar } from '../art/FlowCar'
import { BrandMark } from '../ui/BrandMark'
import { Icon } from '../ui/Icon'

export function RepLogin({ onToken, notice }: { onToken: (token: string) => void; notice?: string }) {
  const [token, setToken] = useState('')
  const [show, setShow] = useState(false)
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (token.trim()) onToken(token.trim())
  }
  return (
    <div className="rep-login on-studio">
      <FlowCar mode="login" />
      <form onSubmit={submit} className="rep-login-card">
        <span className="rep-login-mark" aria-hidden="true"><BrandMark size={40} /></span>
        <h1>פורטל נציגי תחבורה</h1>
        <p className="muted">הזינו את קוד הגישה כדי לראות שאלות פתוחות.</p>
        {notice && <p className="field-error" role="alert"><Icon name="alert" size={16} />{notice}</p>}
        <label htmlFor="rep-token">קוד גישה</label>
        <div className="token-row">
          <input id="rep-token" type={show ? 'text' : 'password'} autoComplete="off" spellCheck={false} value={token}
                 onChange={(e) => setToken(e.target.value)} />
          <button type="button" className="token-eye" onClick={() => setShow((s) => !s)}
                  aria-label={show ? 'הסתרת הקוד' : 'הצגת הקוד'} aria-pressed={show}>
            <Icon name={show ? 'eyeOff' : 'eye'} size={18} />
          </button>
        </div>
        <button type="submit" className="chip chip-primary rep-login-submit" disabled={!token.trim()}>כניסה</button>
        <p className="rep-login-privacy"><Icon name="lock" size={14} />הנציג רואה רק את הקשר הבקשה. נתוני השכר של העובד לא מוצגים.</p>
      </form>
    </div>
  )
}
