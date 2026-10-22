// Transport representative portal. The token lives in sessionStorage and goes out as X-Rep-Token.
import { useEffect, useRef, useState } from 'react'
import { ApiError, repApi, resetRepSession } from '../api/client'
import type { RepQuestion } from '../api/types'
import { REP_LOGOUT_EVENT } from '../shell/repSession'
import { BrandMark } from '../ui/BrandMark'
import { Icon } from '../ui/Icon'
import { QuestionCard } from './QuestionCard'
import { RepLogin } from './RepLogin'
import { RepPoller, browserChannel } from './repPolling'

const TOKEN_KEY = 'sca.repToken'
const BAD_TOKEN = 'קוד הגישה שגוי או שפג תוקפו'
type Tab = 'open' | 'answered'

function readToken(): string | null {
  try { return sessionStorage.getItem(TOKEN_KEY) } catch { return null }
}
function writeToken(token: string | null): void {
  resetRepSession()            // a login or a logout starts a new rep session (its own answer budget)
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
  const [answeredLoaded, setAnsweredLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // the confirmation of the last answer: a counter, so every answer shows it for its own 4 s (a flag let a quick
  // answer's confirmation vanish on the previous one's timer, found by e2e/rep-rate-limit.spec.ts)
  const [success, setSuccess] = useState(0)
  const [pausedFor, setPausedFor] = useState<number | null>(null)
  const poller = useRef<RepPoller | null>(null)
  const logout = useRef(onLogout)
  logout.current = onLogout

  // the list on screen every 5 s, the other one every minute, both after an answer; nothing while the tab is
  // hidden; a 429 pauses it for its Retry-After (repPolling.ts, QA 22/10)
  useEffect(() => {
    const p = new RepPoller({
      fetchOpen: () => repApi.listQuestions(token, 'OPEN'),
      fetchAnswered: () => repApi.listQuestions(token, 'ANSWERED'),
      onOpen: (o) => { setOpen(o); setLoaded(true) },
      onAnswered: (a) => {
        setAnswered([...a].sort((x, y) => (y.answered_at ?? '').localeCompare(x.answered_at ?? '')).slice(0, 20))
        setAnsweredLoaded(true)
      },
      onLoaded: () => setError(null),
      onError: (e) => setError(e instanceof ApiError ? e.message_he : 'משהו השתבש'),
      onUnauthorized: () => logout.current(BAD_TOKEN),
      onPause: setPausedFor,
      channel: browserChannel(),        // other rep tabs of this browser share what they fetch
    })
    poller.current = p
    const onVisibility = () => {
      if (document.visibilityState === 'visible') p.start()
      else p.stop()
    }
    p.start()
    if (document.visibilityState !== 'visible') p.stop()
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      p.dispose()
      poller.current = null
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [token])

  useEffect(() => { poller.current?.setTab(tab) }, [tab])

  useEffect(() => {
    if (!success) return
    const t = window.setTimeout(() => setSuccess(0), 4000)      // restarted by every new answer
    return () => window.clearTimeout(t)
  }, [success])

  const cardProps = {
    token,
    onUnauthorized: () => onLogout(BAD_TOKEN),
    // the server returns the answered question: both lists change here, at no request (the next poll syncs)
    onAnswered: (q: RepQuestion) => {
      setSuccess((n) => n + 1)
      setOpen((o) => o.filter((x) => x.id !== q.id))
      setAnswered((a) => [q, ...a.filter((x) => x.id !== q.id)].slice(0, 20))
    },
    onConflict: () => poller.current?.refreshAll(),          // another rep answered first: both lists now
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
              <dd className="num">{answeredLoaded ? answered.length : '—'}</dd>
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
        {success > 0 && <div className="success-bar" role="status"><Icon name="check" size={18} />התשובה נשלחה לעובד</div>}
        {error && <div className="error-bar" role="alert"><Icon name="alert" size={18} /><span>{error}</span></div>}
        {pausedFor !== null && (
          <div className="error-bar rep-paused" role="status">
            <Icon name="clock" size={18} />
            <span>הרבה בקשות בזמן קצר. הרשימה תתעדכן שוב בעוד {pausedFor} שניות, אפשר להמשיך לעבוד.</span>
          </div>
        )}
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
