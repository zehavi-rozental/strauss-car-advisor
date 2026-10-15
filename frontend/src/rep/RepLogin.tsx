import { useState } from 'react'
import type { FormEvent } from 'react'

export function RepLogin({ onToken, notice }: { onToken: (token: string) => void; notice?: string }) {
  const [token, setToken] = useState('')
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (token.trim()) onToken(token.trim())
  }
  return (
    <div className="rep-login">
      <form onSubmit={submit}>
        <h1>פורטל נציגי תחבורה</h1>
        <p className="muted">הזינו את קוד הגישה כדי לראות שאלות פתוחות.</p>
        {notice && <p className="field-error" role="alert">{notice}</p>}
        <label htmlFor="rep-token">קוד גישה</label>
        <input id="rep-token" type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} />
        <button type="submit" className="chip chip-primary" disabled={!token.trim()}>כניסה</button>
      </form>
    </div>
  )
}
