"""סט הניסוחים: הודעות אמיתיות לכל שאלה, ומה מותר שיקרה למצב בעקבותיהן.

כל קובץ ב-tests/phrasings/<שאלה>.yaml:
  setup: [הודעות שמביאות לשאלה]
  cases:
    - text: "ההודעה"
      allowed: [{שדה: ערך, ...}, ...]    # פעולות מותרות; "שאלה חוזרת" (בלי שינוי) תמיד מותרת

ההשוואה היא על הקרנה של המצב לפני ההודעה ואחריה: כל שינוי חייב להיות בדיוק אחת הפעולות המותרות.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import yaml

from app.modules.conversation.stage import ConversationState

PHRASINGS = Path(__file__).parent / "phrasings"


@dataclass(frozen=True)
class Case:
    file: str
    index: int
    setup: tuple[str, ...]
    text: str
    allowed: tuple[dict[str, Any], ...]


@dataclass
class Tally:
    answered: int = 0
    asked: int = 0
    wrong: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.answered + self.asked + len(self.wrong)


def load_cases(names: Optional[set[str]] = None, first: Optional[int] = None) -> list[Case]:
    out: list[Case] = []
    for path in sorted(PHRASINGS.glob("*.yaml")):
        if names and path.stem not in names:
            continue
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases = data["cases"][:first] if first else data["cases"]
        for i, c in enumerate(cases):
            allowed = tuple({k: norm(v) for k, v in a.items()} for a in c.get("allowed") or [])
            out.append(Case(path.stem, i, tuple(data.get("setup") or []), c["text"], allowed))
    return out


def norm(value: Any) -> Any:
    """ערכים להשוואה: מספרים כמחרוזת עשרונית ("18500", "2.5"), רשימות ממוינות."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        text = format(Decimal(str(value)).normalize(), "f")
        return text
    if isinstance(value, (list, tuple)):
        return sorted(norm(v) for v in value)
    return str(value)


def project(state: ConversationState) -> dict[str, Any]:
    """מה שהעובד אמר, בלי שדות שנגזרים ממנו (קבוצה מהתפקיד, הנעה ושווי שימוש מהרכב שנמצא)."""
    p, c = state.profile, state.current
    car = None
    if c.source == "catalog":
        car = c.catalog_code
    elif c.source == "tax":
        car = f"tax:{c.label}"
    elif c.source == "stated" or (c.source is None and c.query and not c.choices):
        car = f"stated:{c.query}"        # דגם שלא נמצא: נשמר כפי שנכתב, ושואלים על שווי השימוש
    elif c.source == "none":
        car = "none"
    stated = car is not None and car.startswith("stated:")
    pending = state.pending_confirmation
    values = {
        "occupation": p.occupation, "occupation_text": p.occupation_text,
        "occupation_unknown": p.occupation_unknown or None,
        "group": p.group if p.occupation is None else None,
        "base": p.base, "commissions": p.commissions, "bonus": p.bonus,
        "pension": p.pension_percent, "study_fund": p.study_fund_percent,
        "credit_points": p.credit_points, "children": p.children,
        "approximate": p.approximate_fields or None,
        "car": car, "car_choices": len(c.choices) or None,
        "use_value": c.use_value if stated else None, "use_value_unknown": c.use_value_unknown or None,
        "drive": c.drive.value if stated and c.drive else None,
        "copay": c.copay, "copay_unknown": c.copay_unknown or None, "maintenance": c.maintenance,
        "selected": state.selected or None,
        "queued": state.queued_options or None,
        "confirm": f"{pending.field}={norm(pending.value)}" if pending else None,
        "waiting": state.pending_rep is not None or None,
        "clarify": state.pending_clarification.catalog_code if state.pending_clarification else None,
    }
    return {k: norm(v) for k, v in values.items()}


def change(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in after.items() if before.get(k) != v}


def judge(case: Case, diff: dict[str, Any]) -> str:
    """answered | asked | wrong"""
    if not diff:
        return "asked"
    return "answered" if diff in case.allowed else "wrong"


def describe(case: Case, diff: dict[str, Any]) -> str:
    return f"{case.file}#{case.index}: {case.text!r} → {diff}"


def report(title: str, tallies: dict[str, Tally], wrong_label: bool) -> str:
    lines = [title, ""]
    head = f"{'file':<14}{'cases':>6}{'answered':>10}{'asked':>7}" + (f"{'wrong':>7}" if wrong_label else "")
    lines.append(head)
    total = Tally()
    for name, t in sorted(tallies.items()):
        row = f"{name:<14}{t.total:>6}{t.answered:>10}{t.asked:>7}" + (f"{len(t.wrong):>7}" if wrong_label else "")
        lines.append(row)
        total.answered += t.answered
        total.asked += t.asked
        total.wrong += t.wrong
    lines.append(f"{'total':<14}{total.total:>6}{total.answered:>10}{total.asked:>7}"
                 + (f"{len(total.wrong):>7}" if wrong_label else ""))
    if total.wrong:
        lines += ["", "wrong:"] + [f"  {w}" for w in total.wrong]
    return "\n".join(lines) + "\n"
