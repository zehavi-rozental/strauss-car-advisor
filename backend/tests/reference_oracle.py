"""An independent re-implementation of the net method, written from the rules file alone.

It shares no code with `app.modules.calculation`: it reads `strauss-rules.json` and `strauss-data.xlsx`
directly with `json` / `openpyxl` and computes in `Decimal`. `test_reference_oracle.py` compares it with
Strauss's "חישובים" sheet and with the engine, so a bug in the engine and a bug in the oracle would have to
agree to slip through.

    python -m tests.reference_oracle      # print Expected / Oracle / Engine for the six reference employees
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import openpyxl

CONFIG = Path(__file__).resolve().parents[1] / "config"
D = Decimal
CENT = D("0.01")


def _d(v) -> Decimal:
    return D(str(v))


def shekel(v: Decimal) -> Decimal:
    return v.quantize(D(1), rounding=ROUND_HALF_UP)


@dataclass
class Rules:
    budgets: dict[str, Decimal]
    brackets: list[tuple[Decimal | None, Decimal]]
    credit_point: Decimal
    ni: dict[str, Decimal]
    health: dict[str, Decimal]
    pension_rate: Decimal
    pension_max_rate: Decimal
    pension_ceiling: Decimal
    electric_net: Decimal
    family_cars: list[str]
    family_split: Decimal
    version: str


def load_rules() -> Rules:
    r = json.loads((CONFIG / "strauss-rules.json").read_text(encoding="utf-8"))
    nm = r["netMethod"]
    # The oracle implements only the method the delivered file selects; anything else must fail loudly.
    assert nm["contributionsBase"] == "gross"
    assert nm["participation"]["reducesValueOfUse"] is False
    assert r["electric"]["method"] == "gross_up"
    assert nm["basis"] == "monthly"
    pc = nm["pensionCredit"]
    return Rules(
        budgets={k: _d(v) for k, v in r["budgets"].items()},
        brackets=[(None if up is None else _d(up), _d(rate)) for up, rate in nm["brackets"]],
        credit_point=_d(nm["creditPointValue"]),
        ni={k: _d(v) for k, v in nm["nationalInsurance"].items()},
        health={k: _d(v) for k, v in nm["health"].items()},
        pension_rate=_d(pc["rate"]), pension_max_rate=_d(pc["maxContributionRate"]),
        pension_ceiling=_d(pc["incomeCeiling"]),
        electric_net=_d(r["electric"]["netBonus"]),
        family_cars=list(r["familyTrack"]["cars"]),
        family_split=_d(r["familyTrack"]["gapSplitEmployee"]),
        version=r["version"],
    )


@dataclass
class Car:
    code: str
    group: str
    name: str
    reg_year: int
    drive: str
    use_value: Decimal
    monthly_cost: Decimal
    default_copay: Decimal


@dataclass
class Situation:
    """One side of the comparison: a car (with its copay) or opting out (with the maintenance amount)."""
    label: str
    car: Car | None = None
    copay: Decimal = D(0)
    maintenance: Decimal = D(0)


@dataclass
class Employee:
    id: str
    name: str
    occupation: str
    group: str
    base: Decimal
    commissions: Decimal
    bonus: Decimal
    pension_pct: Decimal
    study_pct: Decimal
    points: Decimal
    children: int
    current_car: str
    current_copay: Decimal


@dataclass
class Reference:
    today: str
    alternative: str
    eligible: str
    net_today: Decimal
    net_alt: Decimal
    delta: Decimal
    tax_today: Decimal
    tax_alt: Decimal
    ni_today: Decimal
    ni_alt: Decimal
    electric_gross: Decimal
    family_share: Decimal


@dataclass
class Data:
    cars: dict[str, Car]
    occupations: dict[str, Decimal]          # name -> opt-out maintenance
    employees: list[Employee]
    references: dict[str, Reference] = field(default_factory=dict)


def load_data() -> Data:
    wb = openpyxl.load_workbook(CONFIG / "strauss-data.xlsx", data_only=True)
    rows = lambda name: [r for r in wb[name].iter_rows(min_row=2, values_only=True) if r[0]]  # noqa: E731
    cars = {}
    for r in rows("קטלוג רכבים"):
        cars[r[4]] = Car(code=r[0], group=r[1], name=r[4], reg_year=int(r[7]), drive=r[8],
                         use_value=_d(r[10]), monthly_cost=_d(r[11]), default_copay=_d(r[12]))
    occupations = {r[0]: _d(r[4] or 0) for r in rows("עיסוקים")}
    employees = [Employee(id=r[0], name=r[1], occupation=r[3], group=r[4], base=_d(r[5]),
                          commissions=_d(r[6] or 0), bonus=_d(r[7] or 0), pension_pct=_d(r[8]),
                          study_pct=_d(r[9]), points=_d(r[10]), children=int(r[11]),
                          current_car=r[12], current_copay=_d(r[13] or 0))
                 for r in rows("עובדים לדוגמה")]        # r[2], the (fictional) ID number, is never read
    refs = {r[0]: Reference(today=r[2], alternative=r[3], eligible=r[4], net_today=_d(r[6]),
                            net_alt=_d(r[7]), delta=_d(r[8]), tax_today=_d(r[9]), tax_alt=_d(r[10]),
                            ni_today=_d(r[11]), ni_alt=_d(r[12]), electric_gross=_d(r[13] or 0),
                            family_share=_d(r[14] or 0))
            for r in rows("חישובים")}
    return Data(cars=cars, occupations=occupations, employees=employees, references=refs)


_OPT_OUT = re.compile(r"(\d[\d,]*)")


def situation(label: str, data: Data, copay: Decimal | None = None) -> Situation:
    """Reads a label of the reference sheet: "ATTO 3 (2025)" or "ויתור - אחזקת רכב 2000 ₪"."""
    if label.startswith("ויתור") or label.startswith("אין"):
        return Situation(label=label, maintenance=_d(_OPT_OUT.search(label).group(1).replace(",", "")))
    name = label.split(" (")[0].strip()
    car = data.cars[name]
    return Situation(label=label, car=car, copay=car.default_copay if copay is None else copay)


@dataclass
class Breakdown:
    cash: Decimal
    use_value: Decimal
    electric_gross: Decimal
    pension: Decimal
    study: Decimal
    tax: Decimal
    ni: Decimal
    health: Decimal
    copay: Decimal
    family_share: Decimal
    net: Decimal


def _two_tier(income: Decimal, t: dict[str, Decimal]) -> Decimal:
    capped = min(income, t["ceiling"])
    low = min(capped, t["threshold"])
    high = max(capped - t["threshold"], D(0))
    return low * t["lowRate"] + high * t["highRate"]


def _bracket_tax(income: Decimal, rules: Rules) -> Decimal:
    tax, lower = D(0), D(0)
    for upper, rate in rules.brackets:
        top = income if upper is None else min(income, upper)
        if top > lower:
            tax += (top - lower) * rate
        if upper is None or income <= upper:
            break
        lower = upper
    return tax


def net_of(emp: Employee, s: Situation, rules: Rules, electric_gross: Decimal = D(0)) -> Breakdown:
    salary = emp.base + emp.commissions + emp.bonus
    pension = salary * emp.pension_pct / 100
    study = salary * emp.study_pct / 100
    cash = salary + s.maintenance + electric_gross
    use_value = s.car.use_value if s.car else D(0)
    taxable = cash + use_value
    credit = rules.pension_rate * min(pension, rules.pension_max_rate * rules.pension_ceiling)
    tax = max(_bracket_tax(taxable, rules) - emp.points * rules.credit_point - credit, D(0))
    ni, health = _two_tier(taxable, rules.ni), _two_tier(taxable, rules.health)
    family = D(0)
    if s.car and s.car.code in rules.family_cars:
        family = max(s.car.monthly_cost - rules.budgets[emp.group], D(0)) * rules.family_split
    net = cash - tax - ni - health - pension - study - s.copay - family
    return Breakdown(cash, use_value, electric_gross, pension, study, tax, ni, health, s.copay, family, net)


def evaluate(emp: Employee, s: Situation, rules: Rules) -> Breakdown:
    """Electric cars get the gross component G with net(G) − net(0) = 250, found by bisection to 1/100 agora."""
    base = net_of(emp, s, rules)
    if not (s.car and s.car.drive == "חשמלי"):
        return base
    lo, hi = D(0), rules.electric_net * 4
    target = base.net + rules.electric_net
    while hi - lo > D("0.0001"):
        mid = (lo + hi) / 2
        if net_of(emp, s, rules, mid).net < target:
            lo = mid
        else:
            hi = mid
    return net_of(emp, s, rules, hi)


def compute_all() -> list[dict]:
    rules, data = load_rules(), load_data()
    out = []
    for emp in data.employees:
        ref = data.references[emp.id]
        today = situation(ref.today, data, copay=emp.current_copay)
        if today.car is None:   # "ויתור - אחזקת רכב 1500 ₪": the current copay is 0
            today.copay = D(0)
        alt = situation(ref.alternative, data)
        out.append(dict(emp=emp, ref=ref, today=evaluate(emp, today, rules), alt=evaluate(emp, alt, rules)))
    return out


def main() -> None:
    for row in compute_all():
        emp, ref, t, a = row["emp"], row["ref"], row["today"], row["alt"]
        print(f"{emp.id} {emp.name}: נטו היום {shekel(t.net)} (שטראוס {ref.net_today}) · "
              f"חלופה {shekel(a.net)} (שטראוס {ref.net_alt}) · "
              f"הפרש {shekel(a.net) - shekel(t.net):+} (שטראוס {ref.delta:+})")


if __name__ == "__main__":
    main()
