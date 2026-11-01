"""מכונת המצבים של השיחה. הקוד — לא מודל השפה — קובע באיזה שלב אנחנו ומה חסר.

השלב נגזר מהנתונים שנאספו: כלי שמעדכן נתונים יכול לקדם את השיחה, אבל אף כלי לא
קובע את השלב ישירות.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional, Sequence

from pydantic import BaseModel, Field

from app.modules.calculation.public import Drive

MAX_OPTIONS = 3
OPT_OUT_ID = "OPT_OUT"
# pending_ambiguity: "אינני משתתף" / a bare "אין" before the car is known — no car, or a car with copay 0? Asked,
# never guessed (the tools and the agent both set it, so it lives here)
NO_CAR_OR_ZERO = "no_car_or_zero_copay"


class Stage(str, Enum):
    COLLECT_PROFILE = "COLLECT_PROFILE"
    COLLECT_CURRENT_CAR = "COLLECT_CURRENT_CAR"
    CHOOSE_OPTIONS = "CHOOSE_OPTIONS"
    CALCULATING = "CALCULATING"
    WAITING_FOR_REP = "WAITING_FOR_REP"
    PRESENTED = "PRESENTED"


class RepReason(str, Enum):
    """שאלות לנציג התחבורה (אותם ערכים כמו MissingField במודול cases)."""

    NEW_CAR_COPAY = "NEW_CAR_COPAY"
    CURRENT_COPAY = "CURRENT_COPAY"            # no longer opened (rule A, 22/10); kept for questions already open
    CURRENT_USE_VALUE = "CURRENT_USE_VALUE"    # no longer opened (rule A, 22/10); kept for questions already open
    OCCUPATION_OPT_OUT = "OCCUPATION_OPT_OUT"
    MAINTENANCE_AMOUNT = "MAINTENANCE_AMOUNT"
    ELIGIBILITY_GROUP = "ELIGIBILITY_GROUP"    # the stated group contradicts the occupations table (C4)


class Profile(BaseModel):
    """פרטי השכר כפי שהעובד מסר. אחוזים נשמרים כאחוזים (6 = 6%)."""

    occupation: Optional[str] = None          # שם מטבלת העיסוקים
    occupation_text: Optional[str] = None     # מה שהעובד כתב, כשלא נמצא בטבלה
    occupation_unknown: bool = False
    group: Optional[str] = None
    base: Optional[Decimal] = None
    commissions: Optional[Decimal] = None
    bonus: Optional[Decimal] = None
    pension_percent: Optional[Decimal] = None
    study_fund_percent: Optional[Decimal] = None
    credit_points: Optional[Decimal] = None
    children: Optional[int] = None
    # תשובות הנציג לעיסוק שאינו בטבלה: מותר לוותר? ובאיזה סכום אחזקה
    rep_opt_out_allowed: Optional[bool] = None
    rep_opt_out_maintenance: Optional[Decimal] = None
    approximate_fields: list[str] = Field(default_factory=list)   # שדות שנאמרו כהערכה ("כ-1,000")
    # Q2 fields the employee said they do not know ("לא יודע"): never a default value. They wait until the
    # end; the calculation does not run without them, and the bot says where to find them.
    unknown_fields: list[str] = Field(default_factory=list)


# The one map between a save_profile argument and the Profile attribute it fills (the seven salary fields).
# Every other map of these fields is derived from it, so a field is never added in one place only.
PROFILE_ARGS: dict[str, str] = {
    "base_salary": "base", "commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
    "study_fund_percent": "study_fund_percent", "credit_points": "credit_points", "children_under_23": "children",
}
ARG_OF: dict[str, str] = {attr: arg for arg, attr in PROFILE_ARGS.items()}   # Profile attribute → argument


def profile_args(p: "Profile") -> dict[str, Any]:
    """The salary fields as save_profile arguments (None = not given)."""
    return {arg: getattr(p, attr) for arg, attr in PROFILE_ARGS.items()}


class CarChoice(BaseModel):
    label: str
    description: str = ""     # קוד הדגם מרישיון הרכב — מבדיל בין גרסאות
    drive: Drive
    use_value: Decimal
    reg_year: int
    catalog_code: Optional[str] = None   # a catalog car ("קורולה": COROLLA or COROLLA CROSS)


class CurrentCar(BaseModel):
    """הרכב של היום. source: catalog / tax / stated (שווי מהתלוש) / none (אין רכב)."""

    query: Optional[str] = None
    reg_year: Optional[int] = None
    source: Optional[Literal["catalog", "tax", "stated", "none"]] = None
    label: Optional[str] = None
    catalog_code: Optional[str] = None
    coverage: Optional[str] = None       # a year outside the tax file: the years it covers ("2019–2026")
    group: Optional[str] = None
    drive: Optional[Drive] = None
    use_value: Optional[Decimal] = None
    use_value_unknown: bool = False
    monthly_cost: Optional[Decimal] = None
    choices: list[CarChoice] = Field(default_factory=list)
    copay: Optional[Decimal] = None
    copay_unknown: bool = False
    # where today's copay came from (1/11): "rep_confirmed" = the representative's answer, applied by the employee
    copay_source: Optional[str] = None
    # When there is no company car today: does the employee receive a car-maintenance allowance?
    # maintenance=Decimal: the confirmed amount (0 = the employee said they do NOT receive one).
    # maintenance=None + maintenance_unknown=True: "אבדוק בהמשך" — unknown, never assumed, blocks the net.
    # maintenance=None + maintenance_unknown=False: not asked yet.
    maintenance: Optional[Decimal] = None
    maintenance_unknown: bool = False
    # "יש לי רכב חברה" before the model is known, or a model the employee said was wrong ("התכוונתי לרכב
    # אחר"): there IS a company car, only the model is missing. Never asked "אם אין רכב" again.
    has_car: bool = False
    rejected: list[str] = Field(default_factory=list)   # catalog codes the employee said are not their car


class PendingRep(BaseModel):
    """השיחה ממתינה לנציג עד שכל השאלות החוסמות נענו. המזהים נרשמים בשלב הכתיבה של התור."""

    reason: RepReason
    question_ids: list[str] = Field(default_factory=list)
    resume_stage: Stage
    labels: list[str] = Field(default_factory=list)   # שמות הרכבים שעליהם נשאל (לתשובה לעובד)


class OptionResult(BaseModel):
    option_id: str
    label: str
    run_id: Optional[str] = None
    result: dict[str, Any]                    # ScenarioResult במבנה JSON


class MapEntry(BaseModel):
    """STRAUSS DRIVE 360°: one eligible option and the engine's result for it.
    status: full (calculated, copay final) / catalog_copay (calculated with the catalog copay, waiting for the
    representative's approval) / incomplete (a value is missing) / not_eligible (the engine's own check)."""

    option_id: str
    label: str
    group: Optional[str] = None
    drive: Optional[str] = None
    status: Literal["full", "catalog_copay", "incomplete", "not_eligible"]
    result: dict[str, Any]


class ExcludedOption(BaseModel):
    """A catalog car the employee cannot choose, and why (from the eligibility rules, never a guess)."""

    label: str
    group: Optional[str] = None
    reason: str


class OptionMap(BaseModel):
    entries: list[MapEntry] = Field(default_factory=list)
    excluded: list[ExcludedOption] = Field(default_factory=list)
    fresh: bool = True        # False once any value the engine uses changes (tools._invalidate_results)


class ConfirmedCopay(BaseModel):
    """השתתפות שהנציג אישר או קבע לחלופה (לפי מזהה קטלוג)."""

    value: Decimal
    effective_date: Optional[date] = None
    changed: bool = False       # הנציג קבע סכום שונה מהקטלוג


class GroupConflict(BaseModel):
    """The group the employee stated and the group of their role in the occupations table differ (C4): neither code
    nor the model decides; the transport representative does. Until then nothing is calculated."""

    stated: str
    table: str
    occupation: str
    announced: bool = False     # the employee was told once that the representative will decide


class PendingOccupation(BaseModel):
    """תפקיד שאינו בטבלת העיסוקים: לא נשמר עד שהעובד מאשר, או בוחר אחת מההצעות הדומות."""

    text: str
    suggestions: list[str] = Field(default_factory=list)   # עד 3 שמות מהטבלה


class PendingConfirmation(BaseModel):
    """סכום מקוצר ("18" לשכר): לא מרחיבים בשקט — שואלים "התכוונתם ל-18,000 ₪?" ושומרים רק אחרי "כן"."""

    field: str              # שם הארגומנט ב-save_profile
    value: Decimal
    approximate: bool = False
    unusual: bool = False   # a legal but rare value: asked "לאשר / לתקן" once
    scale: bool = False     # a tiny amount ("3" for commissions): asked "3 ₪ או 3,000 ₪?"; value is the amount said


class CarClarification(BaseModel):
    """העובד הזכיר רכב ולא ברור אם זה הרכב שלו או חלופה (בהמתנה לנציג, או ב-Q3 כשהרכב של היום עוד לא
    ידוע). שאלנו; זו התשובה שמחכה."""

    catalog_code: str
    label: str
    reg_year: Optional[int] = None      # שנה שנכתבה עם הרכב — להתאמה, אם זה הרכב של היום


class GeneralQuestion(BaseModel):
    """A question sent to the transport representative (GENERAL_QUESTION), by its option_id (a hash of the
    normalized text): asked again, its status or the answer already given is shown, never a second send."""

    text: str                       # what was sent (privacy-checked)
    answer: Optional[str] = None    # the representative's answer, once it arrived


class RepCopayOffer(BaseModel):
    """The representative answered today's copay with an amount (1/11): offered, applied only when the employee
    confirms it. Removed once applied, so a second click, a reload or a reopened conversation never applies it again."""

    question_id: str
    amount: Decimal
    car_label: str = ""


class ConversationState(BaseModel):
    stage: Stage = Stage.COLLECT_PROFILE
    profile: Profile = Field(default_factory=Profile)
    current: CurrentCar = Field(default_factory=CurrentCar)
    selected: list[str] = Field(default_factory=list)
    results: list[OptionResult] = Field(default_factory=list)
    calculated_for: list[str] = Field(default_factory=list)
    pending_rep: Optional[PendingRep] = None
    confirmed_copays: dict[str, ConfirmedCopay] = Field(default_factory=dict)
    # a copay the employee expects for a new car (C1): kept apart, never used for the calculation, sent for checking
    stated_new_copays: dict[str, Decimal] = Field(default_factory=dict)
    # new cars whose copay the representative was asked about with a reason (the employee's request, an
    # arrangement that differs): shown as waiting until answered, even when the catalog amount is final
    rep_copay_asked: list[str] = Field(default_factory=list)
    group_conflict: Optional[GroupConflict] = None
    confirmed_unusual: list[str] = Field(default_factory=list)   # fields whose rare value the employee confirmed
    applied_answers: list[str] = Field(default_factory=list)    # תשובות נציג שכבר יושמו (פעם אחת בלבד)
    rep_copay_offer: Optional[RepCopayOffer] = None             # today's copay from the rep, awaiting the employee
    pending_clarification: Optional[CarClarification] = None
    pending_confirmation: Optional[PendingConfirmation] = None
    pending_occupation: Optional[PendingOccupation] = None
    queued_options: list[str] = Field(default_factory=list)     # חלופות לחישוב אחרי תשובת הנציג
    asked_groups: list[str] = Field(default_factory=list)       # שאלות מקובצות שכבר נשאלו ("Q2")
    role_asked: list[str] = Field(default_factory=list)         # רכבים ששאלנו עליהם "של היום או חלופה?"
    # "הכי משתלם" לפני שהרכב של היום והפרופיל ידועים: נבחר ומחושב ברגע שכל הפרטים ידועים
    rank_requested: bool = False
    last_asked: list[str] = Field(default_factory=list)         # שדות השאלה האחרונה שהוצגה (לזיהוי שאלה חוזרת)
    # Q2 was answered in part (a later turn saved a Q2 field): only then its leftovers move to Q3.
    # A turn that only asked a question ("מה זה נקודות זיכוי?") does not move on.
    q2_answered: bool = False
    ranked_count: int = 0    # "הכי משתלם": how many eligible cars were ranked (0 = the employee chose)
    last_ask: str = ""       # the last question asked (never repeated verbatim twice in a row)
    unknown_told: int = 0    # replies about fields the employee does not know (the wording alternates)
    misses: int = 0          # answers in a row that were not understood (never the same reply twice)
    unanswered_question: Optional[str] = None    # a question the FAQ does not cover: "לשאול את נציג התחבורה"
    # the employee's latest question nothing here answered, kept after its turn: "היית צריך לשאול את הנציג" offers
    # to send exactly that (cleared once it is sent)
    last_rep_question: Optional[str] = None
    pending_rep_question: Optional[str] = None   # the privacy-checked text shown before sending to the rep
    pending_rep_key: Optional[str] = None        # its option_id, from the employee's own words (dedup)
    general_questions: dict[str, GeneralQuestion] = Field(default_factory=dict)   # sent to the rep, by option_id
    offered_rank: bool = False   # the results reply offered "הכי משתלם": "כן" ranks
    rep_skipped: bool = False    # "להמשיך בלי" this turn: the representative is not offered again
    pending_ambiguity: Optional[str] = None      # "no_car_or_zero_copay": asked with two buttons, never guessed
    # the one save_profile field the last reply asked for (None: several or none). A bare value ("34") may
    # only be saved to it, never remapped to another field.
    asked_field: Optional[str] = None
    pace: Literal["normal", "one_at_a_time"] = "normal"   # "שאלה אחת כל פעם": kept until the employee changes it
    brief: bool = False      # "יותר קצר" / "בלי הסברים": short, plain wording, kept for the conversation
    social_told: int = 0     # small-talk replies so far (the wording turns, never the same line twice in a row)
    feedback_told: int = 0   # acknowledgements of feedback about a previous reply (the wording turns)
    links_shown: list[str] = Field(default_factory=list)  # links already offered (each one once)
    link_now: Optional[str] = None                        # the link of the reply on screen (kept on reload)
    # Fields the employee asked to answer later ("את השכר אבדוק אחר כך"). They are skipped as the next
    # question while any other field can still be asked, and come back when nothing else is askable or a
    # calculation needs them. Never a default, never dropped — just postponed. A value given later clears it.
    deferred_fields: list[str] = Field(default_factory=list)
    # STRAUSS DRIVE 360°: "השווה את כל האפשרויות שלי". Kept once asked; recalculated whenever it is out of date
    # and every value it needs is known. Never changes the selection.
    map_requested: bool = False
    option_map: Optional[OptionMap] = None


# ---------- מה חסר ----------

PROFILE_FIELDS = ("base", "commissions", "bonus", "pension_percent", "study_fund_percent",
                  "credit_points", "children")


def missing_profile(p: Profile) -> list[str]:
    missing: list[str] = []
    if p.group is None:
        missing.append("group" if p.occupation_unknown else "occupation")
    missing += [f for f in PROFILE_FIELDS if getattr(p, f) is None]
    return missing


def missing_current_car(c: CurrentCar) -> list[str]:
    if c.choices:
        return ["car_choice"]
    if c.source is None:
        return ["use_value"] if c.query else ["car"]
    if c.source == "none":
        # the maintenance-allowance question: "כן" (an amount) or "לא" (maintenance=0, a confirmed value)
        # answers it. "אבדוק בהמשך" postpones it via deferred_fields (hidden_deferred skips it while
        # alternatives are chosen, and brings it back before the net is shown) — never a silent 0, never a
        # net on an unverified assumption.
        return [] if c.maintenance is not None else ["maintenance"]
    missing: list[str] = []
    if c.use_value is None and not c.use_value_unknown:
        missing.append("use_value")
    if c.source == "stated" and c.drive is None:
        missing.append("drive")
    if c.copay is None and not c.copay_unknown:
        missing.append("copay")
    return missing


def blocking_rep_reason(state: ConversationState) -> Optional[RepReason]:
    """שאלה לנציג שהשיחה לא יכולה להמשיך בלעדיה (מלבד ויתור, שנבדק בחישוב). The car the employee has today, its
    copay and its use value are the employee's own data: when unknown they are asked from the employee, never the
    representative (rule A, 22/10). Only a group that contradicts the occupations table waits for the rep (C4)."""
    if state.group_conflict is not None:
        return RepReason.ELIGIBILITY_GROUP
    return None


def current_unknown(c: CurrentCar) -> list[str]:
    """Values of today's car the employee said they do not know: no calculation without them, never a default."""
    out = []
    if c.use_value_unknown and c.use_value is None:
        out.append("use_value")
    if c.copay_unknown and c.copay is None:
        out.append("copay")
    return out


def map_missing(state: ConversationState) -> list[str]:
    """What the 360° option map still needs: every profile value and today's car (each option is a difference
    from today). Unknown fields count: the map is never built on a guess."""
    return [*missing_profile(state.profile), *missing_current_car(state.current), *current_unknown(state.current)]


def map_ready(state: ConversationState) -> bool:
    """The option map can be built now. The one condition behind both Agent._resolve_map and the "גלו לי את כל
    האפשרויות" button (view `map_ready`), so the button never promises what the server cannot yet do."""
    return not map_missing(state) and blocking_rep_reason(state) is None


# ---------- שאלות מקובצות (כמו בתרחיש הרשמי) ----------
# Q1: קבוצה או תפקיד + שכר בסיס. Q2: רכיבי שכר, הפרשות וילדים. Q3: הרכב היום, ההשתתפות והחלופות —
# ומה שנשאר חסר מ-Q2. בכל שאלה נשאל רק מה שעדיין חסר.
Q1_FIELDS = ("occupation", "group", "base")
Q2_FIELDS = ("commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children")
CAR_DETAILS = ("car_choice", "use_value", "drive", "maintenance")   # שאלות המשך על הרכב — אחת בכל פעם


def _q2_deferred(state: ConversationState) -> bool:
    """Q2 נשאלה ונענתה בחלקה: מה שחסר ממנה עובר ל-Q3 (לא שואלים את Q2 שוב)."""
    p = state.profile
    return "Q2" in state.asked_groups and (bool(p.unknown_fields) or (
        state.q2_answered and any(getattr(p, f) is not None for f in Q2_FIELDS)))


def unknown_missing(p: Profile) -> list[str]:
    """Q2 fields still empty that the employee said they do not know."""
    return [f for f in Q2_FIELDS if getattr(p, f) is None and f in p.unknown_fields]


def open_profile(p: Profile) -> list[str]:
    """What is still to be asked about the profile now (unknown fields wait for the end)."""
    return [f for f in missing_profile(p) if f not in p.unknown_fields]


def needs_opt_out_occupation(state: ConversationState) -> bool:
    """נמסרה רק קבוצה, והעובד בחר לוותר על הרכב: שואלים מה התפקיד (כלל הוויתור נקבע לפיו)."""
    p = state.profile
    return (OPT_OUT_ID in state.selected and p.occupation is None and p.occupation_text is None
            and p.group is not None and state.pending_occupation is None)


def _all_askable(state: ConversationState) -> list[str]:
    """Every field still open now (profile + current car), unknown fields excluded. The raw list before
    deferral is applied — used to decide whether a deferred field can still be postponed."""
    return [*open_profile(state.profile), *missing_current_car(state.current)]


def hidden_deferred(state: ConversationState) -> set[str]:
    """Deferred fields to skip as the next question RIGHT NOW. A field stays hidden only while something
    else is still askable; once the deferred field(s) are all that is left, they come back (returned empty),
    so a deferral never becomes a dead end and never blocks the flow forever."""
    deferred = [f for f in state.deferred_fields if f in _all_askable(state)]
    if not deferred:
        return set()
    others = [f for f in _all_askable(state) if f not in deferred]
    can_choose_options = not state.selected and not state.rank_requested
    # if there is other work to do (another field, or still choosing options), keep the deferred ones hidden
    return set(deferred) if (others or can_choose_options) else set()


def _visible(fields: Sequence[str], hidden: set[str]) -> list[str]:
    return [f for f in fields if f not in hidden]


def next_stage(state: ConversationState) -> Stage:
    """השלב נגזר מהנתונים בלבד. שדות שהעובד ביקש לדחות מדולגים כל עוד יש עבודה אחרת (hidden_deferred)."""
    if state.pending_rep is not None:
        return Stage.WAITING_FOR_REP
    hidden = hidden_deferred(state)
    missing = _visible(open_profile(state.profile), hidden)
    q1 = [f for f in missing if f in Q1_FIELDS]
    q2 = [f for f in missing if f in Q2_FIELDS]
    car_missing = _visible(missing_current_car(state.current), hidden)
    if q1 or (q2 and not _q2_deferred(state)):
        return Stage.COLLECT_PROFILE
    if car_missing:
        return Stage.COLLECT_CURRENT_CAR
    if q2 or needs_opt_out_occupation(state):
        return Stage.COLLECT_PROFILE
    unknown = unknown_missing(state.profile) + current_unknown(state.current)
    if not state.selected and not (state.rank_requested and unknown):
        return Stage.CHOOSE_OPTIONS
    if unknown:
        return Stage.COLLECT_PROFILE       # no calculation without them, and never a default
    if state.calculated_for != state.selected:
        return Stage.CALCULATING
    return Stage.PRESENTED


def results_stale(state: ConversationState) -> bool:
    """The results on record were calculated before the last change (a saved value, another car, another
    selection) and could not be recalculated yet. Kept as history, marked "לא מעודכן"; never a current
    recommendation, never ranked, never quoted as current, never the base of a decision."""
    return bool(state.results) and state.calculated_for != state.selected


def map_stale(state: ConversationState) -> bool:
    """The option map was built before the last change of a value the engine uses."""
    return state.option_map is not None and not state.option_map.fresh


def current_group(state: ConversationState) -> tuple[Optional[str], list[str]]:
    """השאלה המקובצת הנוכחית ושדותיה החסרים: ("Q1" | "Q2" | "Q3" | "car_detail", [...])."""
    stage = next_stage(state)
    p, c = state.profile, state.current
    hidden = hidden_deferred(state)
    missing = _visible(open_profile(p), hidden)
    q1 = [f for f in missing if f in Q1_FIELDS]
    q2 = [f for f in missing if f in Q2_FIELDS]
    options = [] if state.selected or state.rank_requested else ["options"]
    if stage == Stage.COLLECT_PROFILE:
        if q1:
            return "Q1", q1
        if q2 and not _q2_deferred(state):
            return "Q2", q2
        rest = q2 + (["opt_out_occupation"] if needs_opt_out_occupation(state) else []) + options
        return ("Q3", rest) if rest else ("unknown", unknown_missing(p) + current_unknown(c))
    if stage == Stage.COLLECT_CURRENT_CAR:
        car = _visible(missing_current_car(c), hidden)
        if car and car[0] in CAR_DETAILS:
            return "car_detail", [car[0]]
        if car and car[0] == "car":
            car = ["car"] + ([] if c.copay is not None or c.copay_unknown else ["copay"])
        return "Q3", car + options + q2
    if stage in (Stage.CHOOSE_OPTIONS, Stage.PRESENTED):
        return "Q3", ["options"]
    return None, []


def question_fields(state: ConversationState) -> list[str]:
    return current_group(state)[1]


def next_question(state: ConversationState) -> Optional[str]:
    """השדה הנשאל כשנשאל שדה אחד בלבד; None כשהשאלה מקובצת (או שאין שאלה)."""
    fields = question_fields(state)
    return fields[0] if len(fields) == 1 else None


_Q3_TOOLS = ("save_profile", "match_current_car", "list_options", "rank_options", "calculate")


def allowed_tools(state: ConversationState) -> tuple[str, ...]:
    """הכלים שהשלב מתיר. ב-Q3 הודעה אחת יכולה לכלול רכב, השתתפות, חלופות ושדות שנשארו מ-Q2."""
    stage = next_stage(state)
    if stage in (Stage.CALCULATING, Stage.WAITING_FOR_REP):
        return ()
    group = current_group(state)[0]
    if stage == Stage.COLLECT_PROFILE and group in ("Q1", "Q2"):
        return ("save_profile",)
    if stage == Stage.COLLECT_PROFILE or stage == Stage.COLLECT_CURRENT_CAR:
        return _Q3_TOOLS if group == "Q3" else ("match_current_car", "save_profile")
    return ("list_options", "rank_options", "calculate")
