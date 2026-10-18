"""מכונת המצבים של השיחה. הקוד — לא מודל השפה — קובע באיזה שלב אנחנו ומה חסר.

השלב נגזר מהנתונים שנאספו: כלי שמעדכן נתונים יכול לקדם את השיחה, אבל אף כלי לא
קובע את השלב ישירות.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.modules.calculation.public import Drive

MAX_OPTIONS = 3
OPT_OUT_ID = "OPT_OUT"


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
    CURRENT_COPAY = "CURRENT_COPAY"
    CURRENT_USE_VALUE = "CURRENT_USE_VALUE"
    OCCUPATION_OPT_OUT = "OCCUPATION_OPT_OUT"
    MAINTENANCE_AMOUNT = "MAINTENANCE_AMOUNT"


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


class CarChoice(BaseModel):
    label: str
    description: str = ""     # קוד הדגם מרישיון הרכב — מבדיל בין גרסאות
    drive: Drive
    use_value: Decimal
    reg_year: int


class CurrentCar(BaseModel):
    """הרכב של היום. source: catalog / tax / stated (שווי מהתלוש) / none (אין רכב)."""

    query: Optional[str] = None
    reg_year: Optional[int] = None
    source: Optional[Literal["catalog", "tax", "stated", "none"]] = None
    label: Optional[str] = None
    catalog_code: Optional[str] = None
    group: Optional[str] = None
    drive: Optional[Drive] = None
    use_value: Optional[Decimal] = None
    use_value_unknown: bool = False
    monthly_cost: Optional[Decimal] = None
    choices: list[CarChoice] = Field(default_factory=list)
    copay: Optional[Decimal] = None
    copay_unknown: bool = False
    maintenance: Optional[Decimal] = None     # כשאין רכב היום: אחזקה בתלוש


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


class ConfirmedCopay(BaseModel):
    """השתתפות שהנציג אישר או קבע לחלופה (לפי מזהה קטלוג)."""

    value: Decimal
    effective_date: Optional[date] = None
    changed: bool = False       # הנציג קבע סכום שונה מהקטלוג


class PendingOccupation(BaseModel):
    """תפקיד שאינו בטבלת העיסוקים: לא נשמר עד שהעובד מאשר, או בוחר אחת מההצעות הדומות."""

    text: str
    suggestions: list[str] = Field(default_factory=list)   # עד 3 שמות מהטבלה


class PendingConfirmation(BaseModel):
    """סכום מקוצר ("18" לשכר): לא מרחיבים בשקט — שואלים "התכוונת ל-18,000 ₪?" ושומרים רק אחרי "כן"."""

    field: str              # שם הארגומנט ב-save_profile
    value: Decimal
    approximate: bool = False
    unusual: bool = False   # a legal but rare value: asked "לאשר / לתקן" once


class CarClarification(BaseModel):
    """העובד הזכיר רכב ולא ברור אם זה הרכב שלו או חלופה (בהמתנה לנציג, או ב-Q3 כשהרכב של היום עוד לא
    ידוע). שאלנו; זו התשובה שמחכה."""

    catalog_code: str
    label: str
    reg_year: Optional[int] = None      # שנה שנכתבה עם הרכב — להתאמה, אם זה הרכב של היום


class ConversationState(BaseModel):
    stage: Stage = Stage.COLLECT_PROFILE
    profile: Profile = Field(default_factory=Profile)
    current: CurrentCar = Field(default_factory=CurrentCar)
    selected: list[str] = Field(default_factory=list)
    results: list[OptionResult] = Field(default_factory=list)
    calculated_for: list[str] = Field(default_factory=list)
    pending_rep: Optional[PendingRep] = None
    confirmed_copays: dict[str, ConfirmedCopay] = Field(default_factory=dict)
    confirmed_unusual: list[str] = Field(default_factory=list)   # fields whose rare value the employee confirmed
    applied_answers: list[str] = Field(default_factory=list)    # תשובות נציג שכבר יושמו (פעם אחת בלבד)
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
    misses: int = 0          # answers in a row that were not understood (never the same reply twice)
    unanswered_question: Optional[str] = None    # a question the FAQ does not cover: "לשאול את נציג התחבורה"
    pending_rep_question: Optional[str] = None   # the privacy-checked text shown before sending to the rep
    pending_ambiguity: Optional[str] = None      # "no_car_or_zero_copay": asked with two buttons, never guessed


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
    """שאלה לנציג שהשיחה לא יכולה להמשיך בלעדיה (מלבד ויתור, שנבדק בחישוב)."""
    c = state.current
    if c.use_value_unknown and c.use_value is None:
        return RepReason.CURRENT_USE_VALUE
    if c.copay_unknown and c.copay is None:
        return RepReason.CURRENT_COPAY
    return None


# ---------- שאלות מקובצות (כמו בתרחיש הרשמי) ----------
# Q1: קבוצה או תפקיד + שכר בסיס. Q2: רכיבי שכר, הפרשות וילדים. Q3: הרכב היום, ההשתתפות והחלופות —
# ומה שנשאר חסר מ-Q2. בכל שאלה נשאל רק מה שעדיין חסר.
Q1_FIELDS = ("occupation", "group", "base")
Q2_FIELDS = ("commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children")
CAR_DETAILS = ("car_choice", "use_value", "drive", "maintenance")   # שאלות המשך על הרכב — אחת בכל פעם


def _q2_deferred(state: ConversationState) -> bool:
    """Q2 נשאלה ונענתה בחלקה: מה שחסר ממנה עובר ל-Q3 (לא שואלים את Q2 שוב)."""
    p = state.profile
    return "Q2" in state.asked_groups and state.q2_answered and any(getattr(p, f) is not None for f in Q2_FIELDS)


def needs_opt_out_occupation(state: ConversationState) -> bool:
    """נמסרה רק קבוצה, והעובד בחר לוותר על הרכב: שואלים מה התפקיד (כלל הוויתור נקבע לפיו)."""
    p = state.profile
    return (OPT_OUT_ID in state.selected and p.occupation is None and p.occupation_text is None
            and p.group is not None and state.pending_occupation is None)


def next_stage(state: ConversationState) -> Stage:
    """השלב נגזר מהנתונים בלבד."""
    if state.pending_rep is not None:
        return Stage.WAITING_FOR_REP
    missing = missing_profile(state.profile)
    q1 = [f for f in missing if f in Q1_FIELDS]
    q2 = [f for f in missing if f in Q2_FIELDS]
    if q1 or (q2 and not _q2_deferred(state)):
        return Stage.COLLECT_PROFILE
    if missing_current_car(state.current):
        return Stage.COLLECT_CURRENT_CAR
    if q2 or needs_opt_out_occupation(state):
        return Stage.COLLECT_PROFILE
    if not state.selected:
        return Stage.CHOOSE_OPTIONS
    if state.calculated_for != state.selected:
        return Stage.CALCULATING
    return Stage.PRESENTED


def current_group(state: ConversationState) -> tuple[Optional[str], list[str]]:
    """השאלה המקובצת הנוכחית ושדותיה החסרים: ("Q1" | "Q2" | "Q3" | "car_detail", [...])."""
    stage = next_stage(state)
    p, c = state.profile, state.current
    missing = missing_profile(p)
    q1 = [f for f in missing if f in Q1_FIELDS]
    q2 = [f for f in missing if f in Q2_FIELDS]
    options = [] if state.selected or state.rank_requested else ["options"]
    if stage == Stage.COLLECT_PROFILE:
        if q1:
            return "Q1", q1
        if q2 and not _q2_deferred(state):
            return "Q2", q2
        return "Q3", q2 + (["opt_out_occupation"] if needs_opt_out_occupation(state) else []) + options
    if stage == Stage.COLLECT_CURRENT_CAR:
        car = missing_current_car(c)
        if car[0] in CAR_DETAILS:
            return "car_detail", [car[0]]
        if car[0] == "car":
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
