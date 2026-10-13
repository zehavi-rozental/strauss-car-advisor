"""הממשק הציבורי של מודול הכללים: גרסה פעילה + המרות לטיפוסי המנוע."""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Optional

from app.modules.calculation.public import (
    FAMILY_GROUP,
    CarState,
    CopayStatus,
    Employee,
    OccupationRule,
    OptOutState,
    State,
)

from .loader import ConfigError, RulesSnapshot, RulesStore, load_snapshot
from .models import CatalogCar, Occupation, ReferenceEmployee, ReferenceResult


def car_state(car: CatalogCar, *, copay: Optional[Decimal] = None,
              copay_status: CopayStatus = CopayStatus.CONFIRMED) -> CarState:
    """רכב מהקטלוג כמצב למנוע.

    בלי השתתפות מפורשת — ברירת המחדל מהקטלוג, מסומנת "ממתינה לאישור נציג".
    עם השתתפות מפורשת — הסטטוס שנמסר (אושר ע"י נציג / נמסר ע"י העובד).
    """
    if copay is None:
        copay, copay_status = car.default_copay, CopayStatus.CATALOG_DEFAULT_PENDING
    return CarState(
        label=car.label,
        group=car.group,
        drive=car.drive,
        use_value=car.use_value,
        monthly_cost=car.monthly_cost,
        copay=copay,
        copay_status=copay_status,
    )


def occupation_rule(occ: Optional[Occupation]) -> Optional[OccupationRule]:
    if occ is None:
        return None
    return OccupationRule(name=occ.name, group=occ.group, car_mandatory=occ.car_mandatory,
                          opt_out_allowed=occ.opt_out_allowed, maintenance=occ.maintenance)


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-zא-ת]+", "", text.lower())


def catalog_names(car: CatalogCar) -> list[str]:
    """השמות שבהם עובד יכול לכנות רכב: הדגם, בלי "BYD", והכינויים מ-extensions.yaml."""
    return [car.model_name, car.model_name.replace("BYD ", ""), *car.aliases]


def find_catalog_car(snapshot: RulesSnapshot, text: str) -> list[CatalogCar]:
    """חיפוש רכב בקטלוג לפי שם, כינוי או מזהה. התאמה מדויקת קודמת להתאמה חלקית."""
    key = _norm(re.sub(r"\(\d{4}\)", "", text))
    if not key:
        return []
    exact = [c for c in snapshot.catalog
             if key == _norm(c.catalog_code) or key in {_norm(n) for n in catalog_names(c)}]
    if exact:
        return exact
    return [c for c in snapshot.catalog
            if any(key in _norm(n) or _norm(n) in key for n in catalog_names(c))]


def candidate_states(snapshot: RulesSnapshot) -> list[State]:
    """כל החלופות האפשריות: כל רכבי הקטלוג + ויתור (הזכאות נבדקת במנוע)."""
    return [car_state(c) for c in snapshot.catalog] + [OptOutState()]


def reference_employee(ref: ReferenceEmployee) -> Employee:
    return Employee(
        group=ref.group, base=ref.base, commissions=ref.commissions, bonus=ref.bonus,
        pension_rate=ref.pension_percent / 100, study_fund_rate=ref.study_fund_percent / 100,
        credit_points=ref.credit_points, eligible_children=ref.children,
    )


_MAINT = re.compile(r"(\d[\d,]*)\s*₪")


def parse_reference_state(snapshot: RulesSnapshot, label: str, *, current_copay: Optional[Decimal] = None,
                          for_current: bool = False) -> State:
    """מפרש תיאור מצב מהאקסל ("TUCSON (2025)", "אין (אחזקה 1500 ₪)", "ויתור - אחזקת רכב 2000 ₪")."""
    if label.startswith("אין") or label.startswith("ויתור"):
        m = _MAINT.search(label)
        maintenance = Decimal(m.group(1).replace(",", "")) if m else None
        return OptOutState(maintenance=maintenance if for_current else None)
    cars = find_catalog_car(snapshot, label)
    if len(cars) != 1:
        raise ConfigError(f"לא נמצא רכב יחיד בקטלוג עבור '{label}'")
    if for_current:
        return car_state(cars[0], copay=current_copay, copay_status=CopayStatus.EMPLOYEE_STATED)
    return car_state(cars[0])


__all__ = [
    "FAMILY_GROUP", "CatalogCar", "ConfigError", "Occupation", "ReferenceEmployee", "ReferenceResult",
    "RulesSnapshot", "RulesStore", "candidate_states", "car_state", "catalog_names", "find_catalog_car",
    "load_snapshot", "occupation_rule", "parse_reference_state", "reference_employee",
]
