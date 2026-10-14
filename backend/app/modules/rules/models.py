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


class Extensions(_Strict):
    """רק מה שקובץ הכללים של שטראוס עדיין לא אומר."""

    version: str
    family_gap_adds_to_copay: bool
    copay_policy: Literal["CATALOG_DEFAULT_CONFIRM_ASYNC", "CATALOG_DEFAULT", "REP_REQUIRED"]
    plug_in_counts_as_electric: bool
    child_age_inclusive: bool
    default_vehicle_type: str
    electric_fuel_codes: list[str]
    car_aliases: dict[str, list[str]] = Field(default_factory=dict)   # מזהה קטלוג → כינויים בעברית


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
    default_copay: Decimal
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
