// Transport representative portal. The token lives in sessionStorage and goes out as X-Rep-Token.
import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError, repApi } from '../api/client'
import type { RepQuestion } from '../api/types'
import { REP_LOGOUT_EVENT } from '../shell/TopBar'
import { BrandMark } from '../ui/BrandMark'
import { Icon } from '../ui/Icon'
import { QuestionCard } from './QuestionCard'
import { RepLogin } from './RepLogin'

const TOKEN_KEY = 'sca.repToken'
const POLL_MS = 3000
const BAD_TOKEN = 'קוד הגישה שגוי או שפג תוקפו'
type Tab = 'open' | 'answered'

function readToken(): string | null {
  try { return sessionStorage.getItem(TOKEN_KEY) } catch { return null }
}
function writeToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    // no storage: the token lives only in memory
  }
}

export function RepPage() {
  const [token, setToken] = useState<string | null>(readToken)
  const [notice, setNotice] = useState<string | undefined>()
  // "יציאה מאזור הנציג" from the user menu in the top bar
  useEffect(() => {
    const out = () => { writeToken(null); setNotice(undefined); setToken(null) }
    window.addEventListener(REP_LOGOUT_EVENT, out)
    return () => window.removeEventListener(REP_LOGOUT_EVENT, out)
  }, [])
  if (!token) {
    return <RepLogin notice={notice} onToken={(t) => { writeToken(t); setNotice(undefined); setToken(t) }} />
  }
  return <Portal token={token} onLogout={(message) => { writeToken(null); setNotice(message); setToken(null) }} />
}

function Portal({ token, onLogout }: { token: string; onLogout: (message?: string) => void }) {
  const [tab, setTab] = useState<Tab>('open')
  const [open, setOpen] = useState<RepQuestion[]>([])
  const [answered, setAnswered] = useState<RepQuestion[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const alive = useRef(true)

  const refresh = useCallback(async () => {
    try {
      const [o, a] = await Promise.all([repApi.listQuestions(token, 'OPEN'), repApi.listQuestions(token, 'ANSWERED')])
      if (!alive.current) return
      setOpen(o)
      setAnswered([...a].sort((x, y) => (y.answered_at ?? '').localeCompare(x.answered_at ?? '')).slice(0, 20))
      setError(null)
      setLoaded(true)
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onLogout(BAD_TOKEN)
      else if (alive.current) setError(e instanceof ApiError ? e.message_he : 'משהו השתבש')
    }
  }, [token, onLogout])

  // refresh every 3s while the tab is visible
  useEffect(() => {
    alive.current = true
    let timer: number | undefined
    const start = () => {
      if (timer === undefined && document.visibilityState === 'visible') {
        timer = window.setInterval(() => void refresh(), POLL_MS)
      }
    }
    const stop = () => {
      if (timer !== undefined) window.clearInterval(timer)
      timer = undefined
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') { void refresh(); start() } else stop()
    }
    void refresh()
    start()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      alive.current = false
      stop()
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [refresh])

  useEffect(() => {
    if (!success) return
    const t = window.setTimeout(() => setSuccess(false), 4000)
    return () => window.clearTimeout(t)
  }, [success])

  const cardProps = {
    token,
    onUnauthorized: () => onLogout(BAD_TOKEN),
    onAnswered: () => { setSuccess(true); void refresh() },
    onConflict: () => void refresh(),
  }
  const emptyOpen = loaded && tab === 'open' && open.length === 0
  const list = tab === 'open' ? open : answered

  return (
    <div className="rep-page">
      <header className="rep-header">
        <div className="rep-header-inner">
          <div className="rep-heading">
            <p className="rep-kicker"><Icon name="rep" size={16} />מסך הנציג</p>
            <h1>פורטל נציגי תחבורה</h1>
          </div>
          <dl className="rep-stats">
            <div className={open.length ? 'has-open' : undefined}>
              <dt>שאלות פתוחות</dt>
              <dd className="num">{loaded ? open.length : '—'}</dd>
            </div>
            <div>
              <dt>נענו</dt>
              <dd className="num">{loaded ? answered.length : '—'}</dd>
            </div>
          </dl>
          <button type="button" className="link-button rep-logout" onClick={() => onLogout()}>
            <Icon name="logout" size={18} />יציאה
          </button>
        </div>
        <nav className="rep-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'open'} className={tab === 'open' ? 'on' : ''}
                  onClick={() => setTab('open')}>שאלות פתוחות <span className="count num">{open.length}</span></button>
          <button type="button" role="tab" aria-selected={tab === 'answered'} className={tab === 'answered' ? 'on' : ''}
                  onClick={() => setTab('answered')}>נענו</button>
        </nav>
      </header>
      <main className="rep-main">
        {success && <div className="success-bar" role="status"><Icon name="check" size={18} />התשובה נשלחה לעובד</div>}
        {error && <div className="error-bar" role="alert"><Icon name="alert" size={18} /><span>{error}</span></div>}
        {!loaded && !error && (
          <div className="rep-skeleton" aria-hidden="true"><i /><i /></div>
        )}
        {emptyOpen && (
          <div className="empty-state">
            <BrandMark size={56} />
            <p>אין שאלות פתוחות</p>
          </div>
        )}
        {list.map((q) => <QuestionCard key={q.id} question={q} {...cardProps} />)}
        {emptyOpen && answered.length > 0 && (
          <section className="rep-recent">
            <h2 className="rep-subtitle">נענו לאחרונה</h2>
            {answered.slice(0, 5).map((q) => <QuestionCard key={q.id} question={q} {...cardProps} />)}
          </section>
        )}
      </main>
    </div>
  )
}
