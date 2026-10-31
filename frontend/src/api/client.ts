// The only place that talks to the backend. Relative /api/... paths, cookie included.
import type {
  ApiErrorBody,
  CarSearchItem,
  CatalogItem,
  ConversationView,
  EngineState,
  ExtractionResult,
  MessageIn,
  QuestionStatus,
  RepAnswerIn,
  RepQuestion,
  RepQuestionIn,
  RepQuestionResult,
  TurnResponse,
  UpdatesResponse,
} from './types'

const GENERIC_ERROR = 'משהו השתבש. נסו שוב בעוד רגע.'
const NETWORK_ERROR = 'אין חיבור לשרת. בדקו את החיבור ונסו שוב.'

export class ApiError extends Error {
  readonly status: number
  readonly message_he: string
  readonly errors: Record<string, string>     // per field (the profile form), empty otherwise
  readonly retryAfter: number | null          // a 429's Retry-After, in seconds

  constructor(status: number, message_he: string, errors: Record<string, string> = {},
              retryAfter: number | null = null) {
    super(message_he)
    this.status = status
    this.message_he = message_he
    this.errors = errors
    this.retryAfter = retryAfter
  }
}

/** Retry-After in whole seconds (the server sends seconds); null when absent or not a number. */
export function retryAfterSeconds(value: string | null): number | null {
  const n = value === null ? NaN : Number(value)
  return Number.isFinite(n) && n > 0 ? Math.ceil(n) : null
}

function errorMessage(body: ApiErrorBody | null): string {
  if (body?.message_he) return body.message_he
  if (typeof body?.detail === 'string') return body.detail
  return GENERIC_ERROR
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  // FormData sets its own multipart boundary; only a JSON string body gets the JSON content type.
  const jsonBody = typeof init.body === 'string'
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      credentials: 'include',
      headers: { Accept: 'application/json', ...(jsonBody ? { 'Content-Type': 'application/json' } : {}),
                 ...init.headers },
    })
  } catch {
    throw new ApiError(0, NETWORK_ERROR)
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) {
    throw new ApiError(response.status, errorMessage(body), body?.errors ?? {},
                       retryAfterSeconds(response.headers.get('Retry-After')))
  }
  return body as T
}

const conv = (id: string) => `/api/conversations/${encodeURIComponent(id)}`

export const api = {
  /** The engine's real state (this request makes no model call). */
  health: () => request<{ engine?: EngineState }>('/api/health'),
  createConversation: () => request<TurnResponse>('/api/conversations', { method: 'POST' }),
  getConversation: (id: string) => request<ConversationView>(conv(id)),
  sendMessage: (id: string, body: MessageIn) =>
    request<TurnResponse>(`${conv(id)}/messages`, { method: 'POST', body: JSON.stringify(body) }),
  getUpdates: (id: string, after: number) => request<UpdatesResponse>(`${conv(id)}/updates?after=${after}`),
  /** Send an employee-initiated question to the transport representative. Uses the existing cases mechanism;
   *  the server classifies by intent (re-validated against the conversation) and returns an honest status. */
  repQuestion: (id: string, body: RepQuestionIn) =>
    request<RepQuestionResult>(`${conv(id)}/rep-question`, { method: 'POST', body: JSON.stringify(body) }),
  /** A new verification link for the address this conversation waits to verify: "queued" | "valid" | "limit" |
   *  "verified" (what happened, for the status line). */
  resendVerification: (id: string) =>
    request<{ resend: string }>(`${conv(id)}/email/verify`, { method: 'POST', body: JSON.stringify({}) }),
  redeemEmailLink: (token: string) =>
    request<{ conversation_id: string; question_id: string | null }>('/api/conversations/return', {
      method: 'POST', body: JSON.stringify({ token }),
    }),
  searchCars: (q: string) => request<CarSearchItem[]>(`/api/catalog/search?q=${encodeURIComponent(q)}`),
  /** The company catalog (GET /api/catalog), for display details on the selection cards only. */
  catalog: () => request<CatalogItem[]>('/api/catalog'),
  /** Upload a payslip for extraction only. The file is never stored; the result is reviewed and
   *  confirmed before anything is saved. */
  extractPayslip: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ExtractionResult>('/api/payslip/extract', { method: 'POST', body: form })
  },
}

// This screen's rep session: a random id made at login, sent as X-Rep-Session. The server counts each rep's
// answers by it, so reps on one network (one address) do not share an answer budget. Not a credential: the
// token is still checked first, and the address keeps a ceiling whatever id is sent.
const REP_SESSION_KEY = 'sca.repSession'
let memorySession: string | null = null

function newSessionId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID()
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join('')
}

/** The session id of this tab (made on first use, kept for the tab's life). */
export function repSessionId(): string {
  try {
    let id = sessionStorage.getItem(REP_SESSION_KEY)
    if (!id) {
      id = newSessionId()
      sessionStorage.setItem(REP_SESSION_KEY, id)
    }
    return id
  } catch {
    memorySession ??= newSessionId()      // no storage: one id for as long as the page lives
    return memorySession
  }
}

/** Logging in or out starts a new session. */
export function resetRepSession(): void {
  memorySession = null
  try {
    sessionStorage.removeItem(REP_SESSION_KEY)
  } catch {
    // no storage: the memory id above was reset
  }
}

const repHeaders = (token: string) => ({ 'X-Rep-Token': token, 'X-Rep-Session': repSessionId() })

/** Representative API: the token goes in X-Rep-Token on every call. A 401 means the token is wrong. */
export const repApi = {
  listQuestions: (token: string, status: QuestionStatus) =>
    request<RepQuestion[]>(`/api/rep/questions?status=${status}`, { headers: repHeaders(token) }),
  answer: (token: string, id: string, body: RepAnswerIn) =>
    request<RepQuestion>(`/api/rep/questions/${encodeURIComponent(id)}/answer`, {
      method: 'POST', headers: repHeaders(token), body: JSON.stringify(body),
    }),
}
