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
  | 'ELIGIBILITY_GROUP'

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
  question?: string | null   // ask_rep: the employee's question the inquiry card opens with ("" — still to be said)
  // copay: today's car and its (unverified) catalog amount; rep_copay: the rep's amount and the one it replaces
  context?: { car?: string; catalog_amount?: string; amount?: string; previous?: string | null; provisional?: string | null }
}

export interface ExpectsLink {
  kind: 'profile_form' | 'selection' | 'payslip_upload'
  label: string
  url?: string               // payslip_upload only: a pilot's external URL; empty uses the built-in upload
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

export type CopaySource = 'catalog' | 'catalog_final' | 'representative' | 'missing'

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
  /** where the new car's copay comes from: the catalog default waiting for the representative ("catalog"), the
   *  representative's amount ("representative", from copay_effective_date), no catalog amount ("missing": not
   *  calculated), the catalog as final ("catalog_final") or none (opting out). An older server sends nothing. */
  copay_source?: CopaySource | null
  copay_effective_date?: string | null       // ISO date the representative's amount applies from
  employee_stated_copay?: Decimal            // what the employee said they expect: shown, never used
  /** today's copay is pending with the representative: today's net used this catalog amount, provisionally (1/11) */
  today_copay_provisional?: Decimal
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
  rep_question_id?: string | null
}

export interface RepInquiry {
  id: string
  missing_field: string
  question_text: string          // the representative's full text (never shown to the employee)
  employee_text?: string | null  // what the employee wrote; null for a question the system opened itself
  option_id?: string | null      // the car of a copay inquiry
  car_label: string | null
  status: 'OPEN' | 'ANSWERED' | 'CANCELLED'
  created_at: string
  answered_at: string | null
  answer_value: string | null
  answer_note: string | null
}

/** What really answers the conversation (server-side, from the provider chain and each turn): never from a key alone. */
export type EngineState = 'ai' | 'untried' | 'limited' | 'none'

export interface TurnResponse {
  conversation_id: string
  stage: Stage
  reply: string
  source: string
  engine?: EngineState | null   // what answered this turn; null: code alone (buttons, a form)
  results: FlatOptionResult[]  // only when this turn calculated
  pending_rep: RepReason | null
  expects: Expects
  results_stale?: boolean     // the results predate the last change: history, not a recommendation
}

export interface ConversationView {
  conversation_id: string
  stage: Stage
  profile: Record<string, unknown>
  current_car: Record<string, unknown>
  today_copay?: string | null   // today's copay as the employee gave or confirmed it
  today_copay_provisional?: string | null   // pending with the rep: the catalog amount used meanwhile
  selected: string[]
  results: OptionResult[]
  pending_rep: RepReason | null
  expects: Expects
  messages: Message[]
  rep_questions?: RepInquiry[]
  results_stale?: boolean     // the results predate the last change: history, not a recommendation
  option_map?: OptionMap | null      // 360°: null until the employee asks for it
  map_ready?: boolean         // the option map can be built now (an older server does not say: not offered)
  map_missing?: string[]      // what the map still needs, in Hebrew
  rep_email?: string | null
  rep_email_verified?: boolean
  rep_email_delivery?: EmailDelivery
  rep_email_pending?: boolean   // an address waits for its verification (a new link may be offered)
  rep_email_simulated?: boolean // the fake mail backend (a local test server): no real email is sent
}

/** 'disabled': mail is not configured — the inquiry and the answer stay in the chat and nothing mentions email */
export type EmailDelivery = 'enabled' | 'disabled'

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
  rep_questions?: RepInquiry[]
  expects: Expects
  results: OptionResult[]
  today_copay?: string | null   // today's copay as the employee gave or confirmed it
  today_copay_provisional?: string | null   // pending with the rep: the catalog amount used meanwhile
  results_stale?: boolean     // the results predate the last change: history, not a recommendation
  option_map?: OptionMap | null      // 360°: null until the employee asks for it
  map_ready?: boolean         // the option map can be built now (an older server does not say: not offered)
  map_missing?: string[]      // what the map still needs, in Hebrew
  rep_email?: string | null
  rep_email_verified?: boolean
  rep_email_delivery?: EmailDelivery
  rep_email_pending?: boolean
  rep_email_simulated?: boolean
}

export interface CarSearchItem {
  id: string
  label: string
  source: 'catalog' | 'tax'
  group: string | null
  drive: Drive | string | null
}

/** GET /api/catalog: one company car. Shown on the selection cards; never used to calculate anything. */
export interface CatalogItem {
  code: string               // the same id as expects.choices (CAR-01 …)
  label: string
  group: string
  drive: Drive | string
  use_value: Decimal          // monthly taxable use value (tax authority)
  default_copay: Decimal      // the catalog copay, until the representative confirms another amount
}

/** The structured profile form (Q2 / "תיקון נתונים"). Only the fields that are sent are saved;
 *  the server checks the ranges (422) and saves without the LLM. Keys are the server's names. */
export interface ProfileFields {
  base_salary?: number       // from a reviewed payslip
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
  | { fields: ProfileFields; source?: 'payslip' }     // source: values approved from a reviewed payslip

// ---------- employee-initiated rep question (POST /conversations/{id}/rep-question) ----------

/** intent decides the kind, not the car: 'general' is informational (never changes the calculation);
 *  'copay_verification' is a structured copay check the server re-validates against the car on screen. */
export type RepQuestionIntent = 'general' | 'copay_verification' | 'current_copay'

export interface RepQuestionIn {
  question: string
  intent: RepQuestionIntent
  option_id?: string
  email?: string             // only while mail delivery is on and the address is not verified yet
}

/** The server's honest outcome: 'created' (a new question was saved), 'exists' (an open one already held — the
 *  saved text is returned, the new wording was NOT sent), 'invalid' (not an authorised car). missing_field is
 *  internal (GENERAL_QUESTION / NEW_CAR_COPAY) and never shown to the employee. */
export interface RepQuestionResult {
  status: 'created' | 'exists' | 'invalid'
  missing_field: string
  question_id: string | null
  option_id: string | null
  car_label: string | null
  catalog_amount: string | null
  question_text: string | null
  results: FlatOptionResult[]      // the current results, so the client can reflect copay_pending honestly
  email_verified?: boolean
  email_status?: 'verified' | 'sent' | 'pending' | 'failed' | 'disabled'
  email_delivery?: EmailDelivery
}

// ---------- payslip upload (backend/app/modules/payslip) ----------

/** One field read from the payslip. value is null when missing or not confident (never assume 0).
 *  Only the supported profile fields are ever returned; never name, ID, or base salary. */
export interface ExtractedField {
  field: keyof ProfileFields
  label: string              // Hebrew label for the review form
  value: Decimal | null      // string number, or null when missing / ambiguous
  kind: 'amount' | 'percent' | 'count'
  confident: boolean         // false → show as "לא ודאי", needs manual review
  in_range: boolean          // passed the extensions.yaml range check
  note: string               // short Hebrew note (e.g. "מחוץ לטווח, נא לבדוק")
  status?: 'found' | 'missing' | 'ambiguous'
  source_label?: string      // the label as printed on the payslip ("שכר יסוד")
  certainty?: 'high' | 'medium' | 'low' | ''
}

/** What the payslip shows that is not entered automatically (an unclassified addition, the pension as an
 *  amount, gross, net, taxes), with the reason. Never saved. */
export interface PayslipInfo {
  key: string
  label: string
  value: Decimal
  kind: 'amount' | 'percent' | 'count'
  source_label: string
  note: string
  suspect?: boolean       // the value does not look right for this line: shown as doubtful, never as a fact
  unclear?: boolean       // an addition whose label does not say whether it is pay or a deduction
  assignable?: boolean    // the server allows assigning it to an empty commissions / bonus row (never car items)
}

export type PayslipStatus = 'found' | 'partial' | 'nothing_found' | 'not_payslip' | 'unreadable' | 'failed'

/** POST /payslip/extract. The payslip itself is never stored; saving happens through {fields}
 *  only after the employee reviews, edits, and confirms. ok=false (status "failed") → retry or manual entry. */
export interface ExtractionResult {
  ok: boolean
  status?: PayslipStatus      // absent from an older server
  reason?: string             // why it failed: timeout / quota / unavailable / rejected / invalid_response / not_configured
  retry?: boolean             // trying the same file again may help
  fields: ExtractedField[]
  info?: PayslipInfo[]
  message_he: string
}

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
  /** group, car_label, catalog_code, suggested_amount, occupation_text, stated_group, table_group,
   *  employee_stated_amount; never salary or profile data */
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
  errors?: Record<string, string>     // the profile form: every field outside its range
}

// ---------- STRAUSS DRIVE 360° (backend/app/modules/conversation/option_map.py) ----------

/** full: calculated, copay final · catalog_copay: calculated with the catalog copay, waiting for the rep ·
 *  incomplete: a value is missing (never ranked) · not_eligible: the engine's own eligibility check */
export type MapStatus = 'full' | 'catalog_copay' | 'incomplete' | 'not_eligible'

export interface MapEntry {
  option_id: string
  label: string
  group: string | null
  drive: string | null
  status: MapStatus
  result: ScenarioResult
}

export interface OptionMap {
  entries: MapEntry[]               // calculated (net, high to low), then waiting, then not eligible
  excluded: { label: string; group: string | null; reason: string }[]
  counts: { eligible: number; calculated: number; waiting: number; catalog_copay: number }
  leaders: string[]                 // highest estimated net among the calculated; several = a tie; [] = none
  stale: boolean                    // built before the last change: history, never a current ranking
  group: string | null
}
