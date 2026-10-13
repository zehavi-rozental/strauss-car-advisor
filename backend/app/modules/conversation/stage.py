"""מכונת המצבים של השיחה. הקוד — לא מודל השפה — קובע באיזה שלב אנחנו ומה חסר.

השלב נגזר מהנתונים שנאספו: כלי שמעדכן נתונים יכול לקדם את השיחה, אבל אף כלי לא
קובע את השלב ישירות.
"""
from __future__ import annotations

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
    """שאלות חוסמות לנציג התחבורה."""

    CURRENT_COPAY = "CURRENT_COPAY"
    CURRENT_USE_VALUE = "CURRENT_USE_VALUE"
    OCCUPATION_OPT_OUT = "OCCUPATION_OPT_OUT"


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
    reason: RepReason
    question_id: str
    resume_stage: Stage


class OptionResult(BaseModel):
    option_id: str
    label: str
    run_id: Optional[str] = None
    result: dict[str, Any]                    # ScenarioResult במבנה JSON


class ConversationState(BaseModel):
    stage: Stage = Stage.COLLECT_PROFILE
    profile: Profile = Field(default_factory=Profile)
    current: CurrentCar = Field(default_factory=CurrentCar)
    selected: list[str] = Field(default_factory=list)
    results: list[OptionResult] = Field(default_factory=list)
    calculated_for: list[str] = Field(default_factory=list)
    pending_rep: Optional[PendingRep] = None


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


def next_stage(state: ConversationState) -> Stage:
    """השלב נגזר מהנתונים בלבד."""
    if state.pending_rep is not None:
        return Stage.WAITING_FOR_REP
    if missing_profile(state.profile):
        return Stage.COLLECT_PROFILE
    if missing_current_car(state.current):
        return Stage.COLLECT_CURRENT_CAR
    if not state.selected:
        return Stage.CHOOSE_OPTIONS
    if state.calculated_for != state.selected:
        return Stage.CALCULATING
    return Stage.PRESENTED


def next_question(state: ConversationState) -> Optional[str]:
    """השדה הבא שנשאל עליו (מצב גיבוי: שאלה אחת בכל פעם)."""
    stage = next_stage(state)
    if stage == Stage.COLLECT_PROFILE:
        return missing_profile(state.profile)[0]
    if stage == Stage.COLLECT_CURRENT_CAR:
        return missing_current_car(state.current)[0]
    if stage in (Stage.CHOOSE_OPTIONS, Stage.PRESENTED):
        return "options"
    return None


ALLOWED_TOOLS: dict[Stage, tuple[str, ...]] = {
    Stage.COLLECT_PROFILE: ("save_profile",),
    Stage.COLLECT_CURRENT_CAR: ("match_current_car", "save_profile"),
    Stage.CHOOSE_OPTIONS: ("list_options", "rank_options", "calculate"),
    Stage.CALCULATING: (),
    Stage.WAITING_FOR_REP: (),
    Stage.PRESENTED: ("list_options", "rank_options", "calculate"),
}
