// Mirror of the backend responses (backend/app/api/routes.py, conversation/public.py, expects.py,
// stage.py, calculation/models.py). Decimal values arrive as strings.

export type Decimal = string

export type Stage =
  | 'COLLECT_PROFILE'
  | 'COLLECT_CURRENT_CAR'
  | 'CHOOSE_OPTIONS'
  | 'CALCULATING'
  | 'WAITING_FOR_REP'
  | 'PRESENTED'

export type RepReason =
  | 'NEW_CAR_COPAY'
  | 'CURRENT_COPAY'
  | 'CURRENT_USE_VALUE'
  | 'OCCUPATION_OPT_OUT'
  | 'MAINTENANCE_AMOUNT'
  | 'GENERAL_QUESTION'

export type Drive = 'PETROL' | 'HYBRID' | 'PLUG_IN' | 'ELECTRIC'

// ---------- expects ----------

export type ExpectsType = 'choice' | 'multi_choice' | 'car_search' | 'number' | 'none'

export interface Choice {
  id: string
  label: string
  group?: string | null      // options only
  drive?: Drive | null       // options only
}

export interface QuickReply {
  id: string
  label: string
}

export interface ExpectsField {
  field: string | null
  type: ExpectsType
  choices: Choice[]
  quick_replies: QuickReply[]
  max: number | null
  closed?: boolean           // a closed question: the client shows buttons; open questions are typed
}

export interface ExpectsLink {
  kind: 'profile_form' | 'selection'
  label: string
}

export interface Expects extends ExpectsField {
  fields: ExpectsField[]
  link?: ExpectsLink | null   // the one secondary link the reply names ("או למלא בטופס")
  placeholder?: string       // the composer hint, with an example
}

// ---------- ScenarioResult ----------

export type CalcStatus = 'OK' | 'INCOMPLETE' | 'NOT_ELIGIBLE'

export interface Breakdown {
  label: string
  cash_gross: Decimal
  use_value: Decimal
  maintenance: Decimal
  electric_gross_up: Decimal
  pension: Decimal
  study_fund: Decimal
  income_tax: Decimal
  national_insurance: Decimal
  health: Decimal
  copay: Decimal
  family_share: Decimal
  net: Decimal
  net_display: Decimal
}

export interface Line {
  code: string
  label: string
  before: Decimal | null
  after: Decimal | null
  change: Decimal | null
  before_display: Decimal | null
  after_display: Decimal | null
  change_display: Decimal | null
  note: string | null
}

export interface CarCost {
  group: string
  monthly_cost: Decimal
  monthly_cost_display: Decimal
  budget: Decimal
  budget_display: Decimal
  under_budget: boolean
}

export type DriverCode =
  | 'USE_VALUE'
  | 'ELECTRIC_ADDED'
  | 'ELECTRIC_REMOVED'
  | 'COPAY'
  | 'FAMILY_SHARE'
  | 'MAINTENANCE'

export interface Driver {
  code: DriverCode | string
  amount: Decimal
  amount_display: Decimal
  net_effect: Decimal
  net_effect_display: Decimal
  tax_change: Decimal | null
  tax_change_display: Decimal | null
  net: boolean
}

export interface ScenarioResult {
  status: CalcStatus
  eligible: boolean | null
  reason: string
  missing_fields: string[]
  today: Breakdown | null
  alternative: Breakdown | null
  delta_display: Decimal | null
  lines: Line[]
  cost: CarCost | null
  drivers: Driver[]
  electric_refund: Decimal | null
  copay_pending: boolean
  assumptions: string[]
  /** one plain sentence about the main cause (built by the server from the drivers) */
  why?: string
  rules_version: string
  extensions_version: string
  engine_version: string
}

/** GET /conversations/{id} and /updates: nested. */
export interface OptionResult {
  option_id: string
  label: string
  run_id: string | null
  result: ScenarioResult
}

/** POST /conversations and /messages: the ScenarioResult fields are spread into the item. */
export type FlatOptionResult = ScenarioResult & {
  option_id: string
  label: string
  run_id: string | null
}

// ---------- conversation ----------

export interface Message {
  seq: number
  role: 'user' | 'assistant'
  source: string             // user / template / llm / fallback / rep ...
  content: string
  created_at: string         // ISO, UTC
}

export interface TurnResponse {
  conversation_id: string
  stage: Stage
  reply: string
  source: string
  results: FlatOptionResult[]  // only when this turn calculated
  pending_rep: RepReason | null
  expects: Expects
}

export interface ConversationView {
  conversation_id: string
  stage: Stage
  profile: Record<string, unknown>
  current_car: Record<string, unknown>
  selected: string[]
  results: OptionResult[]
  pending_rep: RepReason | null
  expects: Expects
  messages: Message[]
}

export interface PendingQuestion {
  missing_field: RepReason
  mode: 'CONFIRM' | 'REQUIRED'
  status: 'OPEN' | 'ANSWERED' | 'CANCELLED'
  option_id: string | null
}

export interface UpdatesResponse {
  conversation_id: string
  stage: Stage
  messages: Message[]
  pending_questions: PendingQuestion[]
  expects: Expects
  results: OptionResult[]
}

export interface CarSearchItem {
  id: string
  label: string
  source: 'catalog' | 'tax'
  group: string | null
  drive: Drive | string | null
}

/** The structured profile form (Q2 / "תיקון נתונים"). Only the fields that are sent are saved;
 *  the server checks the ranges (422) and saves without the LLM. Keys are the server's names. */
export interface ProfileFields {
  commissions?: number
  bonus?: number
  pension_percent?: number
  study_fund_percent?: number
  credit_points?: number
  children_under_23?: number
}

export type MessageIn =
  | { text: string }
  | { choice: string; field?: string }
  | { choices: string[]; field?: string }
  | { fields: ProfileFields }

// ---------- representative (backend/app/api/rep_routes.py: question_json) ----------

export type AnswerType = 'MONEY' | 'BOOLEAN' | 'DATE' | 'TEXT'
export type QuestionStatus = 'OPEN' | 'ANSWERED' | 'CANCELLED'

export interface RepQuestion {
  id: string
  missing_field: RepReason
  mode: 'CONFIRM' | 'REQUIRED'
  option_id: string | null
  question_text: string
  answer_type: AnswerType
  suggested_value: string | null
  /** group, car_label, catalog_code, suggested_amount, occupation_text; never salary or profile data */
  context: Record<string, string | null>
  status: QuestionStatus
  created_at: string
  answered_at: string | null
  answer_value: string | null
  effective_date: string | null
  answer_note: string | null
  answered_by: string | null
}

export interface RepAnswerIn {
  value: string
  effective_date?: string
  note?: string
}

// ---------- errors ----------

/** The backend today returns FastAPI's default {detail}; the spec's {code, message_he, details}
 *  is accepted too so the client keeps working if the backend moves to it. */
export interface ApiErrorBody {
  detail?: string | { msg: string; loc?: unknown[]; type?: string }[]
  code?: string
  message_he?: string
  details?: unknown
}
