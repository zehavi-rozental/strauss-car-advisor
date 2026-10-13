"""טיפוסי הקלט והפלט של מנוע החישוב.

המנוע הוא ספרייה טהורה: הוא לא יודע מאיפה הגיעו הנתונים (קובץ, מסד, נציג).
כל מה שהוא צריך מגיע כאן כפרמטרים — כולל הכללים עצמם (CalcRules).
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

FAMILY_GROUP = "משפחתי"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


# ---------- כללים ----------

class Bracket(Frozen):
    up_to: Optional[Decimal]  # None = המדרגה העליונה
    rate: Decimal


class TwoTier(Frozen):
    threshold: Decimal
    ceiling: Decimal
    low_rate: Decimal
    high_rate: Decimal


class CalcRules(Frozen):
    """כל מה שהמנוע צריך לדעת על כללי החברה ושיטת הנטו."""

    rules_version: str
    extensions_version: str

    budgets: dict[str, Decimal]
    group_order: list[str]                     # מהנמוכה לגבוהה
    electric_net_bonus: Decimal
    electric_method: Literal["gross_up", "gross"]
    family_min_children: int
    family_gap_employee_share: Decimal
    family_gap_adds_to_copay: bool = True
    plug_in_counts_as_electric: bool = False

    tax_year: int
    credit_point_value: Decimal
    brackets: list[Bracket]
    national_insurance: TwoTier
    health: TwoTier
    pension_credit_rate: Decimal
    pension_credit_income_ceiling: Decimal
    pension_credit_max_rate: Decimal
    pensionable_components: list[Literal["base", "commissions", "bonus"]]
    study_fund_components: list[Literal["base", "commissions", "bonus"]]


# ---------- עובד ----------

class Employee(Frozen):
    """פרופיל שכר. שדה None = עדיין לא ידוע (המנוע יחזיר INCOMPLETE)."""

    group: Optional[str] = None
    base: Optional[Decimal] = None
    commissions: Decimal = Decimal(0)
    bonus: Decimal = Decimal(0)
    pension_rate: Optional[Decimal] = None        # שבר: 0.06 = 6%
    study_fund_rate: Decimal = Decimal(0)         # שבר
    credit_points: Optional[Decimal] = None
    eligible_children: int = 0                    # ילדים עד גיל 23


class OccupationRule(Frozen):
    name: str
    group: str
    car_mandatory: bool
    opt_out_allowed: bool
    maintenance: Decimal


# ---------- מצב רכב (היום או חלופה) ----------

class Drive(str, Enum):
    PETROL = "PETROL"
    HYBRID = "HYBRID"
    PLUG_IN = "PLUG_IN"
    ELECTRIC = "ELECTRIC"


class CopayStatus(str, Enum):
    CATALOG_DEFAULT_PENDING = "CATALOG_DEFAULT_PENDING"   # לפי הקטלוג, ממתין לאישור נציג
    CONFIRMED = "CONFIRMED"                               # אושר ע"י נציג
    EMPLOYEE_STATED = "EMPLOYEE_STATED"                   # מה שהעובד מסר על המצב היום


class CarState(Frozen):
    kind: Literal["car"] = "car"
    label: str
    group: Optional[str] = None             # קבוצת הקטלוג; None לרכב שאינו בקטלוג
    drive: Drive
    use_value: Optional[Decimal] = None     # שווי שימוש חודשי
    monthly_cost: Optional[Decimal] = None  # עלות לחברה (נדרש למסלול המשפחתי)
    copay: Optional[Decimal] = None
    copay_status: CopayStatus = CopayStatus.CONFIRMED


class OptOutState(Frozen):
    kind: Literal["opt_out"] = "opt_out"
    label: str = "ויתור על רכב"
    maintenance: Optional[Decimal] = None   # היום: מה שהעובד מקבל; בחלופה: נקבע לפי העיסוק


State = Union[CarState, OptOutState]


# ---------- פלט ----------

class Status(str, Enum):
    OK = "OK"
    INCOMPLETE = "INCOMPLETE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"


class Breakdown(Frozen):
    """פירוט מלא של הנטו במצב אחד. ערכים לא מעוגלים; העיגול בתצוגה בלבד."""

    label: str
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


class Line(Frozen):
    code: str
    label: str
    before: Optional[Decimal] = None
    after: Optional[Decimal] = None
    note: Optional[str] = None


class ScenarioResult(Frozen):
    status: Status
    eligible: Optional[bool]
    reason: str
    missing_fields: list[str] = Field(default_factory=list)
    today: Optional[Breakdown] = None
    alternative: Optional[Breakdown] = None
    delta_display: Optional[Decimal] = None   # round(נטו חלופה) − round(נטו היום)
    lines: list[Line] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    rules_version: str
    extensions_version: str
    engine_version: str
