"""הודעות תבנית בעברית. משמשות במצב גיבוי, כטיוטה למודל השפה, וכתחליף כשהשומר חוסם.

כל מספר כאן מגיע מתוצאת המנוע, מהפרופיל או מנתוני ההגדרות — לא מחושב כאן.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app.modules.rules.public import RulesSnapshot
from app.shared.money import to_shekel

from .stage import MAX_OPTIONS, ConversationState, RepReason, Stage, next_question
from .tools import available_options

DRIVE_HE = {"PETROL": "בנזין", "HYBRID": "היברידי", "PLUG_IN": "פלאג-אין", "ELECTRIC": "חשמלי"}


def money(value: Any) -> str:
    return f"{to_shekel(Decimal(str(value))):,} ₪"


def signed(value: Any) -> str:
    v = to_shekel(Decimal(str(value)))
    return f"{v:+,} ₪" if v != 0 else "0 ₪"


CAR_AGAIN = "לא זיהיתי את דגם הרכב. כתבו רק את שם הדגם (ושנת רישום אם ידועה), למשל: קורולה קרוס 2023."

GREETING = ("שלום! אני יועץ הרכב של שטראוס. אעזור לך לראות איך שינוי ברכב הצמוד ישפיע על הנטו "
            "החודשי שלך. כל המספרים מחושבים לפי כללי החברה, לא מוערכים.\n")


def question(field: str, state: ConversationState, snapshot: RulesSnapshot) -> str:
    c = state.current
    age = snapshot.strauss.family_track.child_max_age
    groups = ", ".join(snapshot.calc.group_order)
    texts = {
        "occupation": "מה התפקיד שלך בחברה?",
        "group": (f"לא מצאתי את התפקיד \"{state.profile.occupation_text or ''}\" בטבלת העיסוקים. "
                  f"מה קבוצת הזכאות שלך לרכב? ({groups})"),
        "base": "מה שכר הבסיס החודשי שלך (ברוטו)?",
        "commissions": "יש לך עמלות חודשיות? אם כן — כמה בחודש; אם אין, כתבו \"אין\".",
        "bonus": "יש לך בונוס חודשי? אם כן — כמה; אם אין, כתבו \"אין\".",
        "pension_percent": "כמה אחוזים מהשכר מופרשים ממך לפנסיה (חלק העובד)?",
        "study_fund_percent": "כמה אחוזים מופרשים ממך לקרן השתלמות? אם אין, כתבו \"אין\".",
        "credit_points": "כמה נקודות זיכוי יש לך? (מופיע בתלוש)",
        "children": f"כמה ילדים עד גיל {age} יש לך?",
        "car": "איזה רכב צמוד יש לך היום? כתבו את הדגם (ושנת רישום אם ידועה). אם אין לך רכב, כתבו \"אין רכב\".",
        "use_value": (f"לא מצאתי את \"{c.query or ''}\" במאגר. מה שווי השימוש החודשי שמופיע בתלוש שלך? "
                      "אם לא ידוע, כתבו \"לא יודע\"."),
        "drive": "מה סוג ההנעה של הרכב? (בנזין, היברידי, פלאג-אין או חשמלי)",
        "copay": "כמה את/ה משתתף/ת היום בעלות הרכב בכל חודש? אם לא ידוע, כתבו \"לא יודע\".",
        "maintenance": "כמה אחזקת רכב מופיעה היום בתלוש שלך?",
    }
    if field == "car_choice":
        lines = [f"{i}. {ch.label} {ch.description}".rstrip() + f" — שווי שימוש {money(ch.use_value)}"
                 for i, ch in enumerate(c.choices, 1)]
        return ("מצאתי כמה גרסאות של הרכב עם שווי שימוש שונה. איזו מהן שלך? "
                "(קוד הדגם מופיע ברישיון הרכב)\n" + "\n".join(lines))
    if field == "options":
        return options_text(state, snapshot)
    return texts[field]


def options_text(state: ConversationState, snapshot: RulesSnapshot) -> str:
    lines = []
    for i, o in enumerate(available_options(snapshot, state), 1):
        extra = f" — קבוצה {o.group}, {DRIVE_HE[o.drive.value]}" if o.drive else ""
        lines.append(f"{i}. {o.label}{extra}")
    return (f"לאילו חלופות להשוות? אפשר לבחור עד {MAX_OPTIONS} (מספרים או שמות), "
            "או לכתוב \"הכי משתלם\":\n" + "\n".join(lines))


def results_text(state: ConversationState) -> str:
    out: list[str] = []
    assumptions: list[str] = []
    today: Optional[str] = None
    for item in state.results:
        r = item.result
        label = item.label
        if r["today"] and today is None:
            today = money(r["today"]["net_display"])
        if r["status"] == "OK":
            out.append(f"• {label}: הנטו יהיה {money(r['alternative']['net_display'])} — "
                       f"שינוי של {signed(r['delta_display'])} בחודש.")
        elif r["status"] == "NOT_ELIGIBLE":
            out.append(f"• {label}: לא ניתן — {r['reason']}")
        else:
            out.append(f"• {label}: חסר מידע לחישוב — {r['reason']}")
        assumptions += [a for a in r.get("assumptions", []) if a not in assumptions]
    head = f"הנה ההשוואה (הנטו שלך היום: {today}):" if today else "הנה ההשוואה:"
    text = head + "\n" + "\n".join(out)
    if assumptions:
        text += "\n\nהנחות:\n" + "\n".join(f"- {a}" for a in assumptions)
    return text


WAITING = "שאלתי את נציג התחבורה ואעדכן כשתגיע תשובה."
OPT_OUT_DENIED = "לפי נציג התחבורה, בעיסוק שלך לא ניתן לוותר על הרכב"


def _effective(effective: Optional[date]) -> str:
    return f" (בתוקף מ-{effective:%d.%m.%Y})" if effective else ""


def confirmed_assumption(label: str, effective: Optional[date]) -> str:
    return f"השתתפות ב-{label} — אושר על ידי נציג התחבורה{_effective(effective)}"


def rep_confirmed_text(label: str, effective: Optional[date]) -> str:
    return f"נציג התחבורה אישר את ההשתתפות ב-{label}{_effective(effective)}."


def rep_changed_text(label: str, amount: Decimal, effective: Optional[date], old_delta: Any, new_delta: Any) -> str:
    """מספרים מהתשובה ומתוצאות המנוע בלבד. בלי חצים בטקסט בעברית."""
    return (f"נציג התחבורה עדכן את ההשתתפות ב-{label} ל-{money(amount)}{_effective(effective)}. "
            f"השינוי בנטו היה {signed(old_delta)} ועכשיו {signed(new_delta)}.")


def rep_current_text(field: str, label: str, amount: Decimal) -> str:
    what = "ההשתתפות הנוכחית שלך" if field == "CURRENT_COPAY" else "שווי השימוש"
    return f"נציג התחבורה השיב: {what} ב-{label} היא {money(amount)}."


def rep_opt_out_text(allowed: bool) -> str:
    if allowed:
        return ("נציג התחבורה השיב שבעיסוק שלך מותר לוותר על הרכב. "
                "שאלתי אותו מה סכום אחזקת הרכב החודשי; אעדכן כשתגיע תשובה.")
    return f"{OPT_OUT_DENIED}."


def rep_maintenance_text(amount: Decimal) -> str:
    return f"נציג התחבורה השיב: סכום אחזקת הרכב בוויתור הוא {money(amount)} בחודש."


def asked_text(state: ConversationState) -> str:
    """מה נשאל את הנציג, מתוך שדות השאלה בלבד (סוג השאלה ושמות הרכבים)."""
    assert state.pending_rep is not None
    labels = " ו-".join(state.pending_rep.labels)
    topics = {
        RepReason.NEW_CAR_COPAY: f"מה ההשתתפות החודשית ב-{labels}",
        RepReason.CURRENT_COPAY: f"מה ההשתתפות הנוכחית שלך ב-{labels}",
        RepReason.CURRENT_USE_VALUE: f"מה שווי השימוש של {labels}",
        RepReason.OCCUPATION_OPT_OUT: "אם בעיסוק שלך מותר לוותר על הרכב",
        RepReason.MAINTENANCE_AMOUNT: "מה סכום אחזקת הרכב החודשי בוויתור",
    }
    return f"שאלתי את נציג התחבורה {topics[state.pending_rep.reason]}. אעדכן כשתגיע תשובה."


def confirm_car_text(state: ConversationState) -> str:
    """שורת אישור לרכב שזוהה — רק מתוצאת ההתאמה: דגם, קבוצה (לרכב מהקטלוג) והנעה."""
    c = state.current
    details = [c.label or ""]
    if c.group:
        details.append(f"קבוצה {c.group}")
    if c.drive:
        details.append(DRIVE_HE[c.drive.value])
    return f"זיהיתי: {', '.join(details)}. אם זה לא הרכב שלך, כתבו את הדגם המדויק."


def render(state: ConversationState, snapshot: RulesSnapshot, *, understood: bool,
           just_calculated: bool, matched_car: bool = False, notice: Optional[str] = None,
           rep_opened: bool = False) -> str:
    """ההודעה הבאה לפי מצב השיחה בלבד (ומשפט מהקורא, אם יש, לפני השאלה)."""
    parts: list[str] = [confirm_car_text(state)] if matched_car else []
    if notice:
        parts.append(notice)
    waiting = state.stage == Stage.WAITING_FOR_REP
    # השתתפות שהנציג חייב לאשר (REP_REQUIRED): התוצאה מוצגת רק אחרי התשובה
    hide_results = waiting and state.pending_rep is not None and state.pending_rep.reason == RepReason.NEW_CAR_COPAY
    if just_calculated and state.results and not hide_results:
        parts.append(results_text(state))
    if waiting:
        parts.append(asked_text(state) if rep_opened else WAITING)
        return "\n\n".join(parts)
    field = next_question(state)
    if not understood and field == "car":
        parts.append(CAR_AGAIN)
        return "\n\n".join(parts)
    if not understood:
        parts.append("לא הצלחתי להבין את התשובה.")
    if state.stage == Stage.PRESENTED and just_calculated:
        parts.append("רוצה להשוות חלופה נוספת? כתבו את שם הרכב.")
    elif field:
        parts.append(question(field, state, snapshot))
    return "\n\n".join(parts)
