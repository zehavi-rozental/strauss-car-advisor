"""סכמות קבצי ההגדרות. כל קובץ נבדק מול הסכמה בטעינה; ערך כספי חסר = שגיאה."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.calculation.public import Drive


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True)


# ---------- strauss-rules.json (כמו שהוא, בלי שינוי) ----------

class ElectricRule(_Strict):
    net_bonus: Decimal = Field(alias="netBonus")
    method: Literal["gross_up", "gross"]


class FamilyTrackRule(_Strict):
    min_children: int = Field(alias="minChildren")
    child_max_age: int = Field(alias="childMaxAge")
    cars: list[str] = Field(min_length=1)                  # מזהי קטלוג; הרשימה היא המסלול המשפחתי
    price_source: Literal["catalogue"] = Field(alias="priceSource")   # עלות כל רכב לפי הקטלוג
    gap_split_employee: Decimal = Field(alias="gapSplitEmployee")


class WaiverRule(_Strict):
    allowed_by: Literal["occupation"] = Field(alias="allowedBy")
    maintenance_is_taxable: bool = Field(alias="maintenanceIsTaxable")
    maintenance_is_pensionable: bool = Field(alias="maintenanceIsPensionable")

    @model_validator(mode="after")
    def _maintenance_pensionable_unsupported(self):
        # מנוע החישוב מטפל באחזקת הרכב כרכיב לא-פנסיוני בלבד (calculation/tax.py: compute_net מחשב
        # פנסיה וקרן השתלמות על pensionable_components של העובד — base/commissions/bonus — ולעולם לא
        # על האחזקה). maintenanceIsPensionable=true אינו מחובר למנוע, ולכן טעינתו תיצור אי-התאמה שקטה
        # בין ההגדרה לבין החישוב. עד שההתנהגות ל-true תוגדר (פנסיה בלבד? גם קרן השתלמות?) ותמומש עם
        # ערך ייחוס, הטעינה נכשלת בקול — עדיף על חישוב שאינו תואם להגדרה.
        if self.maintenance_is_pensionable:
            raise ValueError(
                "maintenanceIsPensionable=true אינו נתמך עדיין במנוע החישוב; האחזקה מחושבת כרכיב "
                "לא-פנסיוני. נדרש מימוש וערך ייחוס לפני שינוי הערך ל-true."
            )
        return self


class TierRule(_Strict):
    threshold: Decimal
    ceiling: Decimal
    low_rate: Decimal = Field(alias="lowRate")
    high_rate: Decimal = Field(alias="highRate")


class PensionCreditRule(_Strict):
    rate: Decimal
    max_contribution_rate: Decimal = Field(alias="maxContributionRate")
    income_ceiling: Decimal = Field(alias="incomeCeiling")


class ParticipationRule(_Strict):
    treatment: Literal["net_deduction"]
    reduces_value_of_use: bool = Field(alias="reducesValueOfUse")   # true: ההשתתפות מקטינה את שווי השימוש החייב


class RoundingRule(_Strict):
    during: Literal["none"]
    display: Literal["shekel"]


class NetMethodRule(_Strict):
    basis: Literal["monthly"]
    tax_year: int = Field(alias="taxYear")
    credit_point_value: Decimal = Field(alias="creditPointValue")
    brackets: list[tuple[Optional[Decimal], Decimal]]
    national_insurance: TierRule = Field(alias="nationalInsurance")
    health: TierRule
    pension_credit: PensionCreditRule = Field(alias="pensionCredit")
    contributions_base: Literal["gross", "base"] = Field(alias="contributionsBase")   # base: בסיס בלבד
    participation: ParticipationRule
    rounding: RoundingRule

    @model_validator(mode="after")
    def _brackets_ascending(self):
        uppers = [u for u, _ in self.brackets]
        if uppers[-1] is not None or any(u is None for u in uppers[:-1]):
            raise ValueError("רק המדרגה האחרונה יכולה להיות ללא תקרה")
        finite = [u for u in uppers if u is not None]
        if finite != sorted(finite):
            raise ValueError("מדרגות המס חייבות להיות בסדר עולה")
        return self


class StraussRules(_Strict):
    version: str
    budgets: dict[str, Decimal]
    group_order: list[str] = Field(alias="groupOrder")
    choose_from: Literal["own_group_or_lower"] = Field(alias="chooseFrom")
    electric: ElectricRule
    family_track: FamilyTrackRule = Field(alias="familyTrack")
    waiver: WaiverRule
    unused_budget: Literal["not_paid"] = Field(alias="unusedBudget")
    net_method: NetMethodRule = Field(alias="netMethod")

    @model_validator(mode="after")
    def _groups_have_budgets(self):
        missing = [g for g in self.group_order if g not in self.budgets]
        if missing:
            raise ValueError(f"אין תקציב לקבוצות: {missing}")
        return self


# ---------- extensions.yaml ----------

# שדות שעברו לקובץ הכללים של שטראוס. נשארו ב-extensions.yaml → שגיאה, כדי שלא יערכו את הקובץ הלא נכון.
MOVED_TO_RULES = {
    "pension_credit_max_rate": "netMethod.pensionCredit.maxContributionRate",
    "pensionable_components": "netMethod.contributionsBase",
    "study_fund_components": "netMethod.contributionsBase",
    "display_rounding": "netMethod.rounding.display",
}


class CarRoleCues(_Strict):
    """מילים שקובעות אם רכב בהודעה הוא הרכב של היום או חלופה לבדיקה."""

    current: list[str] = Field(default_factory=list)
    alternative: list[str] = Field(default_factory=list)


class Extensions(_Strict):
    """רק מה שקובץ הכללים של שטראוס עדיין לא אומר."""

    version: str
    family_gap_adds_to_copay: bool
    copay_policy: Literal["CATALOG_DEFAULT_CONFIRM_ASYNC", "CATALOG_DEFAULT", "REP_REQUIRED"]
    plug_in_counts_as_electric: bool
    # "להעלות תלוש": offered next to a field the employee does not know.
    # payslip_upload_enabled turns the link on for the built-in uploader (POST /api/payslip/extract).
    # payslip_upload_url points the link at an external pilot page instead; a non-empty URL implies enabled.
    payslip_upload_enabled: bool = False
    payslip_upload_url: str = ""
    child_age_inclusive: bool
    default_vehicle_type: str
    electric_fuel_codes: list[str]
    car_aliases: dict[str, list[str]] = Field(default_factory=dict)   # מזהה קטלוג → כינויים בעברית
    # מילים שמותר שיישארו בהודעה על הרכב הנוכחי אחרי שם הדגם; כל מילה אחרת → מודל השפה
    car_message_fillers: list[str] = Field(default_factory=list)
    # מילים שמסמנות תיקון של הרכב הנוכחי (באחת משלוש המילים הראשונות)
    correction_cues: list[str] = Field(default_factory=list)
    # מילות מילוי לכל סוג שאלה: הקורא פועל רק כשלא נשאר בהודעה דבר מעבר למה שזיהה ולמילים האלה
    profile_fillers: list[str] = Field(default_factory=list)
    answer_fillers: list[str] = Field(default_factory=list)
    option_message_fillers: list[str] = Field(default_factory=list)
    value_qualifiers: list[str] = Field(default_factory=list)  # מילה אחרי ערך מתויג שמשנה את משמעותו
    non_title_words: list[str] = Field(default_factory=list)   # ברכה, שאלה — לא שם תפקיד
    title_words: list[str] = Field(default_factory=list)       # תחילת שם תפקיד, מעבר לטבלת העיסוקים
    car_role_cues: CarRoleCues = Field(default_factory=CarRoleCues)
    # תחילת שם היצרן בקטלוג → מילים שמותר לכתוב לצד הדגם ("BYD אטו 3")
    manufacturer_brands: dict[str, list[str]] = Field(default_factory=dict)
    car_brands: list[str] = Field(default_factory=list)      # יצרנים שאינם בקטלוג: סימן שהטקסט הוא שם רכב
    # טווחים סבירים לכל ערך שנשמר בפרופיל (מכל מקור: הקורא, המודל, כפתורים). מחוץ לטווח — לא נשמר.
    value_ranges: dict[str, tuple[Decimal, Decimal]] = Field(default_factory=dict)
    # legal but rare values that strongly move the net: asked once ("לאשר / לתקן"), never a default.
    # field -> {at_most: N} and/or {at_least: N}
    unusual_values: dict[str, dict[str, Decimal]] = Field(default_factory=dict)
    # a monthly amount below this is asked "3 ₪ או 3,000 ₪?" (field -> limit)
    small_amount_below: dict[str, Decimal] = Field(default_factory=dict)
    pace_cues: list[str] = Field(default_factory=list)          # "שאלה אחת כל פעם"
    pace_reset_cues: list[str] = Field(default_factory=list)    # "הכול ביחד"
    defer_cues: list[str] = Field(default_factory=list)         # "את השכר אבדוק אחר כך"
    all_at_once_cues: list[str] = Field(default_factory=list)   # "תן לי את כל השאלות בפעם אחת"
    option_map_cues: list[str] = Field(default_factory=list)    # STRAUSS DRIVE 360°: "השווה את כל האפשרויות שלי"
    zero_cues: list[str] = Field(default_factory=list)          # a zero the employee said ("אין", "בלי"...)


# ---------- strauss-data.xlsx ----------

DRIVE_FROM_HEBREW = {"בנזין": Drive.PETROL, "היברידי": Drive.HYBRID,
                     "פלאג-אין": Drive.PLUG_IN, "חשמלי": Drive.ELECTRIC}


class CatalogCar(_Strict):
    catalog_code: str
    group: str
    manufacturer: str
    manufacturer_code: str
    model_name: str
    model_code: str
    model_description: str
    reg_year: int
    vehicle_type: str
    drive: Drive
    list_price: Decimal
    use_value: Decimal
    monthly_cost: Decimal
    # the catalog's default employee copay; None when the catalog has none for this car (C2): never invented, the
    # option is not calculated and the transport representative is asked
    default_copay: Optional[Decimal] = None
    aliases: tuple[str, ...] = ()           # מ-extensions.yaml (car_aliases)

    @property
    def label(self) -> str:
        return f"{self.model_name} ({self.reg_year})"


class Occupation(_Strict):
    name: str
    group: str
    car_mandatory: bool
    opt_out_allowed: bool
    maintenance: Decimal


class ReferenceEmployee(_Strict):
    """עובד לדוגמה מהאקסל. מספר ת"ז לא נטען (פרטיות)."""

    employee_id: str
    name: str
    occupation: str
    group: str
    base: Decimal
    commissions: Decimal
    bonus: Decimal
    pension_percent: Decimal
    study_fund_percent: Decimal
    credit_points: Decimal
    children: int
    current_car: str
    current_copay: Decimal
    question: str


class ReferenceResult(_Strict):
    employee_id: str
    today_label: str
    alternative_label: str
    eligible: bool
    reason: str
    net_today: Decimal
    net_alternative: Decimal
    delta: Decimal
    tax_today: Decimal
    tax_alternative: Decimal
    social_today: Decimal
    social_alternative: Decimal
    electric_gross: Decimal
    family_share: Decimal
