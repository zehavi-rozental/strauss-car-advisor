// The only place that talks to the backend. Relative /api/... paths, cookie included.
import type {
  ApiErrorBody,
  CarSearchItem,
  ConversationView,
  MessageIn,
  QuestionStatus,
  RepAnswerIn,
  RepQuestion,
  TurnResponse,
  UpdatesResponse,
} from './types'

const GENERIC_ERROR = 'משהו השתבש. נסו שוב בעוד רגע.'
const NETWORK_ERROR = 'אין חיבור לשרת. בדקו את החיבור ונסו שוב.'

export class ApiError extends Error {
  readonly status: number
  readonly message_he: string

  constructor(status: number, message_he: string) {
    super(message_he)
    this.status = status
    this.message_he = message_he
  }
}

function errorMessage(body: ApiErrorBody | null): string {
  if (body?.message_he) return body.message_he
  if (typeof body?.detail === 'string') return body.detail
  return GENERIC_ERROR
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      credentials: 'include',
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}),
                 ...init.headers },
    })
  } catch {
    throw new ApiError(0, NETWORK_ERROR)
  }
  const body = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(response.status, errorMessage(body))
  return body as T
}

const conv = (id: string) => `/api/conversations/${encodeURIComponent(id)}`

export const api = {
  createConversation: () => request<TurnResponse>('/api/conversations', { method: 'POST' }),
  getConversation: (id: string) => request<ConversationView>(conv(id)),
  sendMessage: (id: string, body: MessageIn) =>
    request<TurnResponse>(`${conv(id)}/messages`, { method: 'POST', body: JSON.stringify(body) }),
  getUpdates: (id: string, after: number) => request<UpdatesResponse>(`${conv(id)}/updates?after=${after}`),
  searchCars: (q: string) => request<CarSearchItem[]>(`/api/catalog/search?q=${encodeURIComponent(q)}`),
}

/** Representative API: the token goes in X-Rep-Token on every call. A 401 means the token is wrong. */
export const repApi = {
  listQuestions: (token: string, status: QuestionStatus) =>
    request<RepQuestion[]>(`/api/rep/questions?status=${status}`, { headers: { 'X-Rep-Token': token } }),
  answer: (token: string, id: string, body: RepAnswerIn) =>
    request<RepQuestion>(`/api/rep/questions/${encodeURIComponent(id)}/answer`, {
      method: 'POST', headers: { 'X-Rep-Token': token }, body: JSON.stringify(body),
    }),
}
