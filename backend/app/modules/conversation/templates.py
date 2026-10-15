"""הודעות תבנית בעברית. ניסוח ניטרלי מגדרית: רבים ("כתבו") או צורות בלי מגדר — אף פעם לא מניחים מגדר. משמשות במצב גיבוי, כטיוטה למודל השפה, וכתחליף כשהשומר חוסם.

כל מספר כאן מגיע מתוצאת המנוע, מהפרופיל או מנתוני ההגדרות — לא מחושב כאן.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional

from app.modules.rules.public import RulesSnapshot
from app.shared.money import to_shekel

from .stage import MAX_OPTIONS, ConversationState, RepReason, Stage, current_group
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
        "occupation": _group_or_occupation(snapshot) + "?",
        "opt_out_occupation": "כדי לבדוק אם אפשר לוותר על הרכב, מה התפקיד שלך?",
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
                      "אם לא ידוע, כתבו \"לא ידוע\"."),
        "drive": "מה סוג ההנעה של הרכב? (בנזין, היברידי, פלאג-אין או חשמלי)",
        "copay": "מה ההשתתפות החודשית שלך היום בעלות הרכב? אם לא ידוע, כתבו \"לא ידוע\".",
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


OPTIONS_ASK = "איזה רכב מעניין אותך — הנה הקטלוג בקבוצה שלך, ואפשר גם מקבוצה נמוכה יותר."


def _option_lines(state: ConversationState, snapshot: RulesSnapshot) -> list[str]:
    lines = []
    for i, o in enumerate(available_options(snapshot, state), 1):
        extra = f" — קבוצה {o.group}, {DRIVE_HE[o.drive.value]}" if o.drive else ""
        lines.append(f"{i}. {o.label}{extra}")
    return lines


def _choose_hint() -> str:
    return f"אפשר לבחור עד {MAX_OPTIONS} (מספרים או שמות), או לכתוב \"הכי משתלם\"."


def options_text(state: ConversationState, snapshot: RulesSnapshot) -> str:
    return "\n".join([OPTIONS_ASK, *_option_lines(state, snapshot), _choose_hint()])


# ---------- שאלות מקובצות (נוסח התרחיש הרשמי; רק מה שעדיין חסר) ----------

def _group_or_occupation(snapshot: RulesSnapshot) -> str:
    order = snapshot.calc.group_order
    # בתרחיש הרשמי השאלה בצורת לוכסן לשני המגדרים; התבניות לא משתמשות בצורות לוכסן — אותה שאלה, בלי מגדר
    return f"מה קבוצת הזכאות שלך ({order[0]}-{order[-1]}), או מה התפקיד שלך"


Q3_CAR = "מה הרכב היום (דגם ושנת רישום) ומה ההשתתפות שלך בו?"
_Q2_PAY = {"commissions": "עמלות", "bonus": "בונוסים"}
_Q2_DEDUCTIONS = {"pension_percent": "פנסיה", "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי"}
# שדה מ-Q2 שנשאר חסר ונשאל בתוך Q3
_LEFTOVER = {"commissions": "יש לך עמלות חודשיות?", "bonus": "יש לך בונוס חודשי?",
             "pension_percent": "כמה אחוזים מופרשים ממך לפנסיה?",
             "study_fund_percent": "כמה אחוזים מופרשים ממך לקרן השתלמות?",
             "credit_points": "כמה נקודות זיכוי יש לך?"}


def _q1(fields: list[str], state: ConversationState, snapshot: RulesSnapshot) -> str:
    if "group" in fields:            # תפקיד שאינו בטבלה (אושר): שואלים את הקבוצה, ואת השכר אם חסר
        return question("group", state, snapshot) + (" ומה שכר הבסיס החודשי שלך?" if "base" in fields else "")
    return f"{_group_or_occupation(snapshot)}? ומה שכר הבסיס החודשי שלך?"


def _q2(fields: list[str], snapshot: RulesSnapshot) -> str:
    age = snapshot.strauss.family_track.child_max_age
    parts = []
    pay = [name for f, name in _Q2_PAY.items() if f in fields]
    if pay:
        parts.append(f"יש רכיבי שכר נוספים — {', '.join(pay)}?")
    deductions = [name for f, name in _Q2_DEDUCTIONS.items() if f in fields]
    if deductions:
        parts.append(f"ואילו הפרשות יורדות לך: {', '.join(deductions)}?")
    if "children" in fields:
        parts.append(f"וכמה ילדים עד גיל {age}?")
    text = " ".join(parts)
    return text[1:] if text.startswith("ו") else text


def _leftovers(fields: list[str], snapshot: RulesSnapshot) -> Optional[str]:
    age = snapshot.strauss.family_track.child_max_age
    asks = [_LEFTOVER[f] for f in fields if f in _LEFTOVER]
    if "children" in fields:
        asks.append(f"כמה ילדים עד גיל {age} יש לך?")
    return f"ועוד: {' '.join(asks)}" if asks else None


def group_question(state: ConversationState, snapshot: RulesSnapshot) -> str:
    """השאלה המקובצת הנוכחית. שדה אחד חסר — השאלה הבודדת שלו."""
    group, fields = current_group(state)
    if not fields:
        return ""
    if len(fields) == 1:
        return question(fields[0], state, snapshot)
    if group == "Q1":
        return _q1(fields, state, snapshot)
    if group == "Q2":
        return _q2(fields, snapshot)
    first: list[str] = []
    if "car" in fields:
        first.append(Q3_CAR)
    elif "copay" in fields:
        first.append(question("copay", state, snapshot))
    if "opt_out_occupation" in fields:
        first.append(question("opt_out_occupation", state, snapshot))
    lines: list[str] = []
    if "options" in fields:
        ask = OPTIONS_ASK if not first else "ו" + OPTIONS_ASK
        lines = [" ".join(first + [ask]), *_option_lines(state, snapshot), _choose_hint()]
    else:
        lines = [" ".join(first)] if first else []
    leftover = _leftovers(fields, snapshot)
    if leftover:
        lines.append(leftover if lines else leftover.removeprefix("ועוד: "))
    return "\n".join(lines)


def _why(drivers: list[dict[str, Any]]) -> list[str]:
    """"למה?" בשתי שורות: הגורמים מהגדול לקטן, כל אחד עם השפעתו על הנטו מהמנוע. הסכומים המוצגים
    מסתכמים בהפרש המוצג."""
    phrases = []
    for d in drivers:
        amount = Decimal(str(d["amount_display"]))
        effect = Decimal(str(d["net_effect_display"]))
        size, net_size = money(abs(amount)), money(abs(effect))
        net_moves = f"הנטו {'עולה' if effect > 0 else 'יורד'} ב-{net_size}"
        code = d["code"]
        if code == "USE_VALUE":
            text = (f"שווי השימוש {'נמוך' if amount < 0 else 'גבוה'} ב-{size} "
                    f"ולכן המס {'יורד' if effect > 0 else 'עולה'} ב-{net_size}")
        elif code in ("ELECTRIC_ADDED", "ELECTRIC_REMOVED"):
            if d.get("net", True):
                text = (f"מתווסף החזר חשמל של {net_size} נטו" if code == "ELECTRIC_ADDED"
                        else f"החזר החשמל של {net_size} נטו יורד")
            else:
                text = (f"{'מתווסף' if code == 'ELECTRIC_ADDED' else 'יורד'} החזר חשמל של {size} ברוטו, "
                        f"ולכן {net_moves}")
        elif code == "MAINTENANCE":
            text = f"אחזקת הרכב {'עולה' if amount > 0 else 'יורדת'} ב-{size}, ואחרי המס {net_moves}"
        else:
            what = "ההשתתפות" if code == "COPAY" else "חלק העובד במסלול המשפחתי"
            verb = ("עולה" if amount > 0 else "יורדת") if code == "COPAY" else ("עולה" if amount > 0 else "יורד")
            text = f"{what} {verb} ב-{size}"
            if abs(effect) != abs(amount):
                text += f" ({net_moves})"
        phrases.append(text)
    if not phrases:
        return []
    lines = ["; ".join(phrases[:2]) + "."]
    if phrases[2:]:
        lines.append("; ".join(phrases[2:]) + ".")
    return ["  למה? " + lines[0], *(f"  {line}" for line in lines[1:])]


def _result_lines(label: str, r: dict[str, Any]) -> list[str]:
    """חלופה אחת: זכאות, עלות מול תקציב, פרטים, נטו משוער והסבר. כל מספר — מתוצאת המנוע."""
    if r["status"] == "NOT_ELIGIBLE":
        return [f"• {label}: לא ניתן — {r['reason']}"]
    if r["status"] != "OK":
        return [f"• {label}: חסר מידע לחישוב — {r['reason']}"]
    out = [f"• {label} — זכאי: {r['reason']}"]
    cost = r.get("cost")
    if cost:
        line = (f"  עלות לחברה {money(cost['monthly_cost_display'])} מול תקציב קבוצה {cost['group']} "
                f"{money(cost['budget_display'])}")
        out.append(line + (" — ההפרש מהתקציב לא משולם בשכר" if cost["under_budget"] else ""))
    t, a = r["today"], r["alternative"]
    details = []
    if Decimal(str(a["use_value"])) or Decimal(str(t["use_value"])):
        details.append(f"שווי שימוש {money(a['use_value'])} (היום {money(t['use_value'])})")
    details.append(f"השתתפות {money(a['copay'])} (היום {money(t['copay'])})")
    if r.get("electric_refund"):
        details.append(f"החזר חשמל {money(r['electric_refund'])} נטו")
    if Decimal(str(a["maintenance"])):
        details.append(f"אחזקת רכב {money(a['maintenance'])}")
    out.append("  " + " · ".join(details))
    out.append(f"  נטו משוער: היום {money(t['net_display'])}, בחלופה {money(a['net_display'])} — "
               f"שינוי של {signed(r['delta_display'])} בחודש.")
    return out + _why(r.get("drivers") or [])


def pending_copay_text(label: str, amount: Any) -> str:
    """נוסח התרחיש הרשמי, כשההשתתפות בחלופה לפי הקטלוג ממתינה לנציג. הסכום — מתוצאת המנוע."""
    return (f"חסר לי אישור לסכום ההשתתפות שלך ב-{label}. חישבתי לפי הסכום בקטלוג ({money(amount)}) "
            "ושלחתי שאלה לנציג התחבורה — החלופה תתעדכן כשתגיע תשובה.")


def results_text(state: ConversationState) -> str:
    out: list[str] = []
    assumptions: list[str] = []
    pending: list[str] = []
    today: Optional[str] = None
    for item in state.results:
        r = item.result
        if r["today"] and today is None:
            today = money(r["today"]["net_display"])
        out += _result_lines(item.label, r)
        shown = r.get("assumptions", [])
        if r.get("copay_pending") and r["status"] == "OK" and r.get("alternative"):
            # במקום שורת ההנחה הכללית — המשפט מהתרחיש (ההנחה עצמה נשארת בתוצאה, לאישור הנציג)
            pending.append(pending_copay_text(item.label, r["alternative"]["copay"]))
            shown = [a for a in shown if a != f"השתתפות ב-{item.label} לפי הקטלוג — ממתינה לאישור נציג"]
        assumptions += [a for a in shown if a not in assumptions]
    head = f"הנה ההשוואה (נטו משוער היום: {today}):" if today else "הנה ההשוואה:"
    text = head + "\n" + "\n".join(out)
    if pending:
        text += "\n\n" + "\n".join(pending)
    if assumptions:
        text += "\n\nהנחות:\n" + "\n".join(f"- {a}" for a in assumptions)
    return text


WAITING = "שאלתי את נציג התחבורה ואעדכן כשתגיע תשובה."

# ---------- ערכים משוערים, סכומים מקוצרים וטווחי סבירות ----------

# שדה ב-save_profile → (שם בתבנית, סוג תצוגה)
_VALUE_LABELS = {
    "base_salary": ("שכר בסיס", "money"), "commissions": ("עמלות", "money"), "bonus": ("בונוס", "money"),
    "pension_percent": ("פנסיה", "percent"), "study_fund_percent": ("קרן השתלמות", "percent"),
    "credit_points": ("נקודות זיכוי", "plain"), "children_under_23": ("ילדים", "plain"),
    "current_use_value": ("שווי שימוש", "money"), "current_copay": ("השתתפות נוכחית", "money"),
    "current_maintenance": ("אחזקת רכב", "money"),
}
# שדה → (למה זה לא סביר, שאלת המשך)
_RANGE_TEXT = {
    "base_salary": ("לשכר בסיס חודשי", "מה הסכום המלא מהתלוש?"),
    "commissions": ("לעמלות חודשיות", "מה הסכום החודשי?"),
    "bonus": ("לבונוס חודשי", "מה הסכום החודשי?"),
    "pension_percent": ("להפרשה לפנסיה", "כמה אחוזים מופרשים ממך לפנסיה?"),
    "study_fund_percent": ("להפרשה לקרן השתלמות", "כמה אחוזים מופרשים ממך לקרן השתלמות?"),
    "credit_points": ("למספר נקודות זיכוי", "כמה נקודות זיכוי מופיעות בתלוש?"),
    "children_under_23": ("למספר ילדים", "כמה ילדים עד גיל 23 יש לך?"),
    "current_use_value": ("לשווי שימוש חודשי", "מה שווי השימוש שמופיע בתלוש?"),
    "current_copay": ("להשתתפות חודשית ברכב", "מה ההשתתפות החודשית שלך?"),
    "current_maintenance": ("לאחזקת רכב חודשית", "מה סכום האחזקה בתלוש?"),
}


def _show(field: str, value: Any) -> str:
    kind = _VALUE_LABELS[field][1]
    number = Decimal(str(value))
    if kind == "money":
        return money(number)
    text = format(number.normalize(), "f")
    return f"{text}%" if kind == "percent" else text


_CONFLICT_LABELS = {
    "occupation": "התפקיד", "eligibility_group": "קבוצת הזכאות", "current_car": "הרכב הנוכחי",
    "options": "החלופות להשוואה", "current_drive": "סוג ההנעה",
}


def conflict_text(field: str) -> str:
    """המודל והקורא הבינו שני ערכים שונים לאותו שדה — לא שומרים אף אחד מהם."""
    label = _CONFLICT_LABELS.get(field) or _VALUE_LABELS.get(field, (field, ""))[0]
    return f"קיבלתי שני ערכים שונים ל{label}. מה הערך הנכון?"


def range_problem_text(field: str, value: Decimal, side: str) -> str:
    """"18 ₪ נראה נמוך מאוד לשכר בסיס חודשי. מה הסכום המלא מהתלוש?" — המספר מההודעה."""
    reason, follow_up = _RANGE_TEXT[field]
    return f"{_show(field, value)} נראה {'נמוך' if side == 'low' else 'גבוה'} מאוד {reason}. {follow_up}"


def confirm_occupation_text(text: str, suggestions: list[str]) -> str:
    if suggestions:
        return (f"לא מצאתי את \"{text}\" בטבלת העיסוקים. התכוונת לאחד מאלה: {', '.join(suggestions)}? "
                f"או כתבו את התפקיד שלך.")
    return f"לא מצאתי את \"{text}\" בטבלת העיסוקים. זה התפקיד שלך? אם לא, כתבו את התפקיד שלך."


def confirm_value_text(field: str, value: Decimal) -> str:
    return f"התכוונת ל-{_show(field, value)}?"


def approximate_assumptions(state: ConversationState) -> list[str]:
    """"עמלות: כ-1,000 ₪, לפי ההערכה שלך" — לכל ערך שהעובד אמר כהערכה."""
    p, c = state.profile, state.current
    values = {"base_salary": p.base, "commissions": p.commissions, "bonus": p.bonus,
              "pension_percent": p.pension_percent, "study_fund_percent": p.study_fund_percent,
              "credit_points": p.credit_points, "children_under_23": p.children,
              "current_use_value": c.use_value, "current_copay": c.copay, "current_maintenance": c.maintenance}
    return [f"{_VALUE_LABELS[f][0]}: כ-{_show(f, values[f])}, לפי ההערכה שלך"
            for f in p.approximate_fields if values.get(f) is not None]


def clarify_car_text(label: str) -> str:
    return (f"לא בטוח שהבנתי: {label} הוא הרכב שלך היום, או חלופה שתרצו לבדוק? "
            f"אם זה הרכב שלך, כתבו \"זה הרכב שלי\". אם זו חלופה, אבדוק אותה ברגע שהנציג יענה.")


def clarify_role_text(label: str) -> str:
    """ב-Q3, כשלא ברור אם הרכב שנכתב הוא הרכב של היום או חלופה."""
    return f"{label} הוא הרכב שלך היום, או חלופה שתרצו לבדוק?"


def queued_option_text(label: str) -> str:
    return f"בסדר, אבדוק את {label} כחלופה ברגע שהנציג יענה."
OPT_OUT_DENIED = "לפי נציג התחבורה, בעיסוק שלך לא ניתן לוותר על הרכב"


def _effective(effective: Optional[date]) -> str:
    return f" (בתוקף מ-{effective:%d.%m.%Y})" if effective else ""


def confirmed_assumption(label: str, effective: Optional[date]) -> str:
    return f"השתתפות ב-{label} — אושר על ידי נציג התחבורה{_effective(effective)}"


def rep_amount_assumption(label: str, amount: Decimal, effective: Optional[date]) -> str:
    """הסכום שהנציג קבע (שונה מהקטלוג) — מופיע בהנחות של כל חישוב של החלופה."""
    return f"השתתפות ב-{label}: {money(amount)} לפי נציג התחבורה{_effective(effective)}"


def rep_confirmed_text(label: str, effective: Optional[date]) -> str:
    return f"נציג התחבורה אישר את ההשתתפות ב-{label}{_effective(effective)}."


def rep_changed_text(label: str, amount: Decimal, effective: Optional[date], old_delta: Any, new_delta: Any) -> str:
    """מספרים מהתשובה ומתוצאות המנוע בלבד. בלי חצים בטקסט בעברית."""
    return (f"נציג התחבורה עדכן את ההשתתפות ב-{label} ל-{money(amount)}{_effective(effective)}. "
            f"השינוי בנטו המשוער היה {signed(old_delta)} ועכשיו {signed(new_delta)}.")


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
           rep_opened: bool = False, notice_asks: bool = False) -> str:
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
    if state.pending_clarification is not None:
        parts.append(clarify_role_text(state.pending_clarification.label))
        return "\n\n".join(parts)
    fields = current_group(state)[1]
    if not understood and fields[:1] == ["car"]:
        parts.append(CAR_AGAIN)
        return "\n\n".join(parts)
    if notice_asks:
        return "\n\n".join(parts)                       # המשפט כבר שואל (בעיית סבירות)
    if state.pending_confirmation is not None:
        pending = state.pending_confirmation
        parts.append(confirm_value_text(pending.field, pending.value))
        return "\n\n".join(parts)
    if state.pending_occupation is not None:
        parts.append(confirm_occupation_text(state.pending_occupation.text, state.pending_occupation.suggestions))
        return "\n\n".join(parts)
    if not understood:
        parts.append("לא הצלחתי להבין את התשובה.")
    if state.stage == Stage.PRESENTED and just_calculated:
        parts.append("אפשר להשוות חלופה נוספת: כתבו את שם הרכב.")
    elif fields:
        parts.append(group_question(state, snapshot))
    return "\n\n".join(parts)
