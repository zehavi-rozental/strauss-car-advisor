"""הודעות תבנית בעברית. ניסוח ניטרלי מגדרית: רבים ("כתבו") או צורות בלי מגדר — אף פעם לא מניחים מגדר. משמשות במצב גיבוי, כטיוטה למודל השפה, וכתחליף כשהשומר חוסם.

כל מספר כאן מגיע מתוצאת המנוע, מהפרופיל או מנתוני ההגדרות — לא מחושב כאן.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.modules.rules.public import RulesSnapshot
from app.shared.money import to_shekel

from .stage import MAX_OPTIONS, Q2_FIELDS, ConversationState, RepReason, Stage, current_group, next_stage
from .tools import available_options

DRIVE_HE = {"PETROL": "בנזין", "HYBRID": "היברידי", "PLUG_IN": "פלאג-אין", "ELECTRIC": "חשמלי"}


def signed(value: Any) -> str:
    """"+611 ₪" / "-50 ₪" / "0 ₪": the sign is explicit; the client shows the minus as "−"."""
    v = to_shekel(Decimal(str(value)))
    return f"{v:+,} ₪" if v != 0 else "0 ₪"


def money(value: Any) -> str:
    return f"{to_shekel(Decimal(str(value))):,} ₪"


CAR_AGAIN = "לא זיהיתי את דגם הרכב."

GREETING = ("שלום! אני יועץ הרכב של שטראוס 🚗\n"
            "אעזור לכם להבין אילו רכבים מתאימים לכם ואיך כל בחירה משפיעה על הנטו שלכם.\n"
            "כדי להתחיל, אשמח לדעת: מה קבוצת הזכאות שלכם (א–ד)? האם יש לכם היום רכב חברה? "
            "והאם יש רכב או סוג רכב שמעניין אתכם — חשמלי, היברידי או משפחתי?\n"
            "אפשר לענות על הכול יחד או בהדרגה, ואת פרטי השכר נשלים בהמשך.")


def question(field: str, state: ConversationState, snapshot: RulesSnapshot) -> str:
    c = state.current
    age = snapshot.strauss.family_track.child_max_age
    groups = ", ".join(snapshot.calc.group_order)
    texts = {
        "occupation": _group_or_occupation(snapshot) + "?",
        "opt_out_occupation": "כדי לבדוק אם אפשר לוותר על הרכב, מה התפקיד שלכם?",
        "group": (f"לא מצאתי את התפקיד \"{state.profile.occupation_text or ''}\" בטבלת העיסוקים. "
                  f"מה קבוצת הזכאות שלכם לרכב? ({groups})"),
        "base": "מה שכר הבסיס החודשי שלכם (ברוטו)?",
        "commissions": "יש לכם עמלות חודשיות? אם כן, כמה בחודש?",
        "bonus": "יש לכם בונוס חודשי? אם כן, כמה?",
        "pension_percent": "כמה אחוזים מהשכר מופרשים מכם לפנסיה (חלק העובד)?",
        "study_fund_percent": "כמה אחוזים מופרשים מכם לקרן השתלמות?",
        "credit_points": "כמה נקודות זיכוי יש לכם? (מופיע בתלוש)",
        "children": f"כמה ילדים עד גיל {age} יש לכם?",
        "car": "איזה רכב צמוד יש לכם היום?",
        "use_value": (f"מאגר רשות המסים כולל רכבים משנות {c.coverage}, ושנת {c.reg_year} לא נמצאת בו. "
                      "מה שווי השימוש החודשי שמופיע בתלוש שלכם?") if c.coverage else
                     f"לא מצאתי את \"{c.query or ''}\" במאגר. מה שווי השימוש החודשי שמופיע בתלוש שלכם?",
        "drive": "מה סוג ההנעה של הרכב: בנזין, היברידי, פלאג-אין או חשמלי?",
        "copay": "מה ההשתתפות החודשית שלכם היום בעלות הרכב?",
        "maintenance": "כמה אחזקת רכב מופיעה היום בתלוש שלכם?",
    }
    if field == "car_choice" and c.choices and all(ch.catalog_code for ch in c.choices):
        names = " או ".join(ch.label for ch in c.choices)
        return f"בקטלוג יש כמה רכבים בשם הזה. הרכב שלכם היום הוא {names}?"
    if field == "car_choice":
        lines = [f"{i}. {ch.label} {ch.description}".rstrip() + f" — שווי שימוש {money(ch.use_value)}"
                 for i, ch in enumerate(c.choices, 1)]
        return ("מצאתי כמה גרסאות של הרכב עם שווי שימוש שונה. איזו מהן שלכם? "
                "קוד הדגם מופיע ברישיון הרכב.\n" + "\n".join(lines))
    if field == "options":
        return options_ask(state, snapshot)
    return texts[field]


OPTIONS_ASK = f"איזה רכבים להשוות? אפשר לבחור עד {MAX_OPTIONS}."


def _option_lines(state: ConversationState, snapshot: RulesSnapshot) -> list[str]:
    lines = []
    for i, o in enumerate(available_options(snapshot, state), 1):
        extra = f" — קבוצה {o.group}, {DRIVE_HE[o.drive.value]}" if o.drive else ""
        lines.append(f"{i}. {o.label}{extra}")
    return lines


def _choose_hint() -> str:
    return "כתבו מספרים או שמות, או \"הכי משתלם\"."


def _options_block(state: ConversationState, snapshot: RulesSnapshot, with_list: bool) -> list[str]:
    """The options travel in `expects.choices` and the client draws them, so the text is the question
    only. The numbered list is the fallback for a client that cannot show choices."""
    return [*_option_lines(state, snapshot), _choose_hint()] if with_list else []


def options_text(state: ConversationState, snapshot: RulesSnapshot, with_list: bool = False) -> str:
    return "\n".join([OPTIONS_ASK, *_options_block(state, snapshot, with_list)])


# ---------- שאלות מקובצות (נוסח התרחיש הרשמי; רק מה שעדיין חסר) ----------

def _group_or_occupation(snapshot: RulesSnapshot) -> str:
    order = snapshot.calc.group_order
    # בתרחיש הרשמי השאלה בצורת לוכסן לשני המגדרים; התבניות לא משתמשות בצורות לוכסן — אותה שאלה, בלי מגדר
    return f"מה קבוצת הזכאות שלכם ({order[0]}-{order[-1]}), או מה התפקיד שלכם"


Q3_CAR = "איזה רכב צמוד יש לכם היום, וכמה משלמים עליו בחודש?"
_Q2_PAY = {"commissions": "עמלות", "bonus": "בונוסים"}
_Q2_DEDUCTIONS = {"pension_percent": "פנסיה", "study_fund_percent": "קרן השתלמות"}
# שדה מ-Q2 שנשאר חסר ונשאל בתוך Q3
_LEFTOVER = {"commissions": "יש לכם עמלות חודשיות?", "bonus": "יש לכם בונוס חודשי?",
             "pension_percent": "כמה אחוזים מופרשים מכם לפנסיה?",
             "study_fund_percent": "כמה אחוזים מופרשים מכם לקרן השתלמות?",
             "credit_points": "כמה נקודות זיכוי יש לכם?"}


_LEFTOVER_NAMES = {"commissions": "עמלות", "bonus": "בונוס", "pension_percent": "פנסיה",
                   "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי", "children": "ילדים"}


def _q1(fields: list[str], state: ConversationState, snapshot: RulesSnapshot) -> str:
    if "group" in fields:            # תפקיד שאינו בטבלה (אושר): שואלים את הקבוצה, ואת השכר אם חסר
        return question("group", state, snapshot) + (" ומה שכר הבסיס החודשי שלכם?" if "base" in fields else "")
    return f"{_group_or_occupation(snapshot)}? ומה שכר הבסיס החודשי שלכם?"


def _q2(fields: list[str], snapshot: RulesSnapshot) -> str:
    age = snapshot.strauss.family_track.child_max_age
    parts = []
    pay = [name for f, name in _Q2_PAY.items() if f in fields]
    if pay:
        parts.append(f"יש רכיבי שכר נוספים — {', '.join(pay)}?")
    deductions = [name for f, name in _Q2_DEDUCTIONS.items() if f in fields]
    if deductions:
        parts.append(f"ואילו הפרשות יורדות לכם: {', '.join(deductions)}?")
    if "credit_points" in fields:
        parts.append("כמה נקודות זיכוי יש לכם?")       # not a deduction: a tax credit
    if "children" in fields:
        parts.append(f"וכמה ילדים עד גיל {age}?")
    text = " ".join(parts)
    return text[1:] if text.startswith("ו") else text


def _leftover_asks(fields: list[str], snapshot: RulesSnapshot) -> list[str]:
    age = snapshot.strauss.family_track.child_max_age
    asks = [_LEFTOVER[f] for f in fields if f in _LEFTOVER]
    if "children" in fields:
        asks.append(f"כמה ילדים עד גיל {age} יש לכם?")
    return asks


def _leftover_tail(fields: list[str]) -> str:
    """Q3's first message names what is still missing from Q2; the client draws the "השלמה" button."""
    return "חסר עוד: " + ", ".join(_LEFTOVER_NAMES[f] for f in fields if f in _LEFTOVER_NAMES) + "."


_ASK_ORDER = ("occupation", "group", "base", "commissions", "bonus", "pension_percent", "study_fund_percent",
              "credit_points", "children", "car", "car_choice", "use_value", "drive", "maintenance", "copay",
              "opt_out_occupation", "options")


def paced(state: ConversationState, fields: Sequence[str]) -> list[str]:
    """The employee asked for one question at a time ("שאלה אחת כל פעם"): only the next field is asked."""
    fields = list(fields)
    if state.pace != "one_at_a_time" or len(fields) <= 1:
        return fields
    if "car" in fields:                     # the car today comes before the Q2 leftovers
        return ["car"]
    return [min(fields, key=lambda f: _ASK_ORDER.index(f) if f in _ASK_ORDER else len(_ASK_ORDER))]


def _once(state: ConversationState, link: Optional[str]) -> Optional[str]:
    """A link is offered once: on the reply that first shows it (and on a reload of that reply)."""
    if link is None or state.pace == "one_at_a_time" and link == "profile_form":
        return None
    return link if link not in state.links_shown or link == state.link_now else None


def group_question(state: ConversationState, snapshot: RulesSnapshot, skip: Sequence[str] = ()) -> str:
    """The current question, one item at a time: Q1, Q2, then the car today (with its copay), then the
    alternatives, then what is left of Q2. A message may still answer more (the parser reads every
    open field); the text asks only the next item. skip: fields asked separately (a follow-up)."""
    group, fields = current_group(state)
    fields = paced(state, [f for f in fields if f not in skip])
    if not fields:
        return ""
    if group == "unknown":
        return unknown_text(state, snapshot, fields)
    if len(fields) == 1:
        return question(fields[0], state, snapshot)
    if group == "Q1":
        return _q1(fields, state, snapshot)
    if group == "unknown":
        return unknown_text(state, snapshot, fields)
    if group == "Q2" or _is_q2(fields):
        return _q2(fields, snapshot)
    if "car" in fields:
        ask = Q3_CAR if "copay" in fields else question("car", state, snapshot)
        # Q2 fields still open are named once (never dropped silently) and asked after the car
        if any(f in Q2_FIELDS for f in fields) and "Q3" not in state.asked_groups:
            ask += "\n" + _leftover_tail(fields)
        return ask
    if "copay" in fields:
        return question("copay", state, snapshot)
    if "opt_out_occupation" in fields:
        return question("opt_out_occupation", state, snapshot)
    if "options" in fields:
        return options_ask(state, snapshot)
    return " ".join(_leftover_asks(fields, snapshot))

def net_change(delta: Any) -> str:
    """"הנטו שלכם יגדל ב-250 ₪ בחודש". The same wording everywhere: future tense, never "ירד"/"עלה"."""
    v = to_shekel(Decimal(str(delta)))
    if v == 0:
        return "הנטו שלכם לא ישתנה"
    return f"הנטו שלכם {'יגדל' if v > 0 else 'יקטן'} ב-{money(abs(v))} בחודש"


_WHY_COMPONENT = {
    "USE_VALUE": ("שווי השימוש", "גבוה", "נמוך", "המס"),
    "MAINTENANCE": ("אחזקת הרכב", "גבוהה", "נמוכה", "הנטו"),
    "COPAY": ("ההשתתפות", "גבוהה", "נמוכה", "הנטו"),
    "FAMILY_SHARE": ("חלק העובד במסלול המשפחתי", "גבוה", "נמוך", "הנטו"),
}


def why_sentence(drivers: list[dict[str, Any]]) -> str:
    """One plain sentence about the main cause (the biggest driver). Words only, no amounts."""
    if not drivers:
        return ""
    d = max(drivers, key=lambda x: abs(Decimal(str(x["net_effect_display"]))))
    amount, effect = Decimal(str(d["amount_display"])), Decimal(str(d["net_effect_display"]))
    code = d["code"]
    grows = effect > 0
    if code == "ELECTRIC_ADDED":
        return "מתווסף החזר חשמל, ולכן הנטו יגדל."
    if code == "ELECTRIC_REMOVED":
        return "החזר החשמל מתבטל, ולכן הנטו יקטן."
    if code in _WHY_COMPONENT:
        name, high, low, target = _WHY_COMPONENT[code]
        if code == "USE_VALUE":
            result = "יקטן" if grows else "יגדל"
        else:
            result = "יגדל" if grows else "יקטן"
        return f"{name} {high if amount > 0 else low} יותר, ולכן {target} {result}."
    return ""


WAITING_COPAY_ONLY = ("חסר לי רק אישור לסכום ההשתתפות שלכם ברכב החדש. "
                      "שאלתי את נציג התחבורה, ואעדכן כאן ברגע שיענה.")
WAITING_COPAY = "תודה! " + WAITING_COPAY_ONLY     # alone (REP_REQUIRED: the result waits for the answer)


def pending_copay_text(label: str, amount: Any) -> str:
    """After a result in the same reply: no "תודה!", straight to what is missing. No numbers."""
    return WAITING_COPAY_ONLY


def _among(n: int) -> str:
    return "בשניהם" if n == 2 else "בכולם"


def _best_line(best: Any) -> str:
    """"הכי משתלם: MG4 (2025), תוספת של 1,013 ₪ בחודש." — one phrase, never the net sentence twice."""
    d = to_shekel(Decimal(str(best.result["delta_display"])))
    if d > 0:
        return f"הכי משתלם: {best.label}, תוספת של {money(d)} בחודש."
    if d < 0:
        return f"הכי משתלם: {best.label}, רק {money(-d)} פחות בחודש."
    return f"הכי משתלם: {best.label}, בלי שינוי בנטו."


def results_text(state: ConversationState) -> str:
    """At most two short lines for a plain comparison: a summary, then what is waiting. The detail
    lives only in the structured results (the summary card and the comparison panel)."""
    ok, bad = [], []
    for item in state.results:
        r = item.result
        (ok if r["status"] == "OK" and r.get("delta_display") is not None else bad).append(item)
    lines: list[str] = []
    best = max(ok, key=lambda i: Decimal(str(i.result["delta_display"]))) if ok else None
    if state.ranked_count and ok:
        # "הכי משתלם": how many were ranked, and that only the top ones are shown
        top = f"אלה {len(ok)} המשתלמים ביותר:" if len(ok) > 1 else "זה המשתלם ביותר:"
        lines.append(f"בדקתי את כל {state.ranked_count} הרכבים שאתם זכאים להם. {top}")
        lines.append(_best_line(best))
    elif len(ok) == 1:
        lines.append(f"בדקתי את {ok[0].label}. {net_change(ok[0].result['delta_display'])}.")
    elif ok:
        deltas = [Decimal(str(i.result["delta_display"])) for i in ok]
        head = f"בדקתי {len(ok)} רכבים"
        if all(d > 0 for d in deltas):
            lines.append(f"{head}, ו{_among(len(ok))} הנטו שלכם יגדל. {_best_line(best)}")
        elif all(d < 0 for d in deltas):
            lines.append(f"{head}, ו{_among(len(ok))} הנטו שלכם יקטן. {_best_line(best)}")
        elif all(d == 0 for d in deltas):
            lines.append(f"{head}, ו{_among(len(ok))} הנטו שלכם לא ישתנה.")
        else:
            lines.append(f"{head}. {_best_line(best)}")
    for item in bad:
        r = item.result
        if r["status"] == "NOT_ELIGIBLE":
            lines.append(f"{item.label} לא זמין לכם: {r['reason']}")
        else:
            lines.append(f"{item.label}: חסר מידע לחישוב. {r['reason']}")
    if any(i.result.get("copay_pending") and i.result["status"] == "OK" for i in ok):
        lines.append(pending_copay_text("", None))
    return "\n".join(lines)


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
    "pension_percent": ("להפרשה לפנסיה", "כמה אחוזים מופרשים מכם לפנסיה?"),
    "study_fund_percent": ("להפרשה לקרן השתלמות", "כמה אחוזים מופרשים מכם לקרן השתלמות?"),
    "credit_points": ("למספר נקודות זיכוי", "כמה נקודות זיכוי מופיעות בתלוש?"),
    "children_under_23": ("למספר ילדים", "כמה ילדים עד גיל 23 יש לכם?"),
    "current_use_value": ("לשווי שימוש חודשי", "מה שווי השימוש שמופיע בתלוש?"),
    "current_copay": ("להשתתפות חודשית ברכב", "מה ההשתתפות החודשית שלכם?"),
    "current_maintenance": ("לאחזקת רכב חודשית", "מה סכום האחזקה בתלוש?"),
}


_RANGE_HIGH = {
    "pension_percent": "לפנסיה. כמה אחוז יורד לכם בפועל?",
    "study_fund_percent": "לקרן השתלמות. כמה אחוז יורד לכם בפועל?",
    "credit_points": "לנקודות זיכוי. כמה נקודות מופיעות בתלוש?",
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
    if side == "high" and field in _RANGE_HIGH:
        return f"{_show(field, value)} גבוה מהטווח שהמערכת מאפשרת {_RANGE_HIGH[field]}"
    return f"{_show(field, value)} נראה {'נמוך' if side == 'low' else 'גבוה'} מאוד {reason}. {follow_up}"


def confirm_occupation_text(text: str, suggestions: list[str]) -> str:
    if suggestions:
        return f"לא מצאתי את \"{text}\" בטבלת העיסוקים. התכוונתם לאחד מאלה: {', '.join(suggestions)}?"
    return f"לא מצאתי את \"{text}\" בטבלת העיסוקים. זה התפקיד שלכם?"


_UNUSUAL_ASK = {
    "pension_percent": ("לאשר פנסיה של {v}?", "רוב השכירים מפרישים לפנסיה."),
    "credit_points": ("לאשר {v} נקודות זיכוי?", "רוב השכירים מקבלים נקודות זיכוי."),
    "base_salary": ("לאשר שכר בסיס של {v}?", "זה נמוך מהמקובל."),
    "children_under_23": ("לאשר {v} ילדים?", "זה מספר גדול של ילדים."),
}


def unusual_fix_text(field: str) -> str:
    """"לתקן" on a rare value: the field stays open and is asked again."""
    return f"בסדר. {_RANGE_TEXT[field][1]}"


def confirm_value_text(field: str, value: Decimal, unusual: bool = False, scale: bool = False) -> str:
    if scale:
        return f"התכוונתם ל-{_show(field, value)} או ל-{_show(field, value * 1000)}?"
    if unusual:
        ask, note = _UNUSUAL_ASK.get(field, (f"לאשר {_VALUE_LABELS[field][0]} {{v}}?", "הערך נדיר."))
        return f"{ask.format(v=_show(field, value))} {note}"
    return f"התכוונתם ל-{_show(field, value)}?"


def approximate_assumptions(state: ConversationState) -> list[str]:
    """"עמלות: כ-1,000 ₪, לפי ההערכה שלכם" — לכל ערך שהעובד אמר כהערכה."""
    p, c = state.profile, state.current
    values = {"base_salary": p.base, "commissions": p.commissions, "bonus": p.bonus,
              "pension_percent": p.pension_percent, "study_fund_percent": p.study_fund_percent,
              "credit_points": p.credit_points, "children_under_23": p.children,
              "current_use_value": c.use_value, "current_copay": c.copay, "current_maintenance": c.maintenance}
    return [f"{_VALUE_LABELS[f][0]}: כ-{_show(f, values[f])}, לפי ההערכה שלכם"
            for f in p.approximate_fields if values.get(f) is not None]


def clarify_car_text(label: str) -> str:
    return (f"לא בטוח שהבנתי: {label} הוא הרכב שלכם היום, או חלופה שתרצו לבדוק? "
            f"אם זה הרכב שלכם, כתבו \"זה הרכב שלי\". אם זו חלופה, אבדוק אותה ברגע שהנציג יענה.")


def clarify_role_text(label: str) -> str:
    """ב-Q3, כשלא ברור אם הרכב שנכתב הוא הרכב של היום או חלופה."""
    return f"{label} הוא הרכב שלכם היום, או חלופה שתרצו לבדוק?"


def queued_option_text(label: str) -> str:
    return f"בסדר, אבדוק את {label} כחלופה ברגע שהנציג יענה."
OPT_OUT_DENIED = "לפי נציג התחבורה, בעיסוק שלכם לא ניתן לוותר על הרכב"


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
    """The demo's key moment: before and after. Numbers come only from the rep's answer, the previous
    result and the new result."""
    since = f", מ-{effective.day}/{effective.month}" if effective else ""
    return (f"הנציג עדכן: השתתפות {money(amount)} בחודש{since}. "
            f"ההפרש עודכן: במקום {signed(old_delta)}, עכשיו {signed(new_delta)} בחודש.")


def rep_current_text(field: str, label: str, amount: Decimal) -> str:
    what = "ההשתתפות הנוכחית שלכם" if field == "CURRENT_COPAY" else "שווי השימוש"
    return f"נציג התחבורה השיב: {what} ב-{label} היא {money(amount)}."


def rep_opt_out_text(allowed: bool) -> str:
    if allowed:
        return ("נציג התחבורה השיב שבעיסוק שלכם מותר לוותר על הרכב. "
                "שאלתי אותו מה סכום אחזקת הרכב החודשי; אעדכן כשתגיע תשובה.")
    return f"{OPT_OUT_DENIED}."


def rep_maintenance_text(amount: Decimal) -> str:
    return f"נציג התחבורה השיב: סכום אחזקת הרכב בוויתור הוא {money(amount)} בחודש."


def asked_text(state: ConversationState) -> str:
    """מה נשאל את הנציג, מתוך שדות השאלה בלבד (סוג השאלה ושמות הרכבים)."""
    assert state.pending_rep is not None
    labels = " ו-".join(state.pending_rep.labels)
    topics = {
        RepReason.NEW_CAR_COPAY: "",
        RepReason.CURRENT_COPAY: f"מה ההשתתפות הנוכחית שלכם ב-{labels}",
        RepReason.CURRENT_USE_VALUE: f"מה שווי השימוש של {labels}",
        RepReason.OCCUPATION_OPT_OUT: "אם בעיסוק שלכם מותר לוותר על הרכב",
        RepReason.MAINTENANCE_AMOUNT: "מה סכום אחזקת הרכב החודשי בוויתור",
    }
    if state.pending_rep.reason == RepReason.NEW_CAR_COPAY:
        return WAITING_COPAY
    return f"שאלתי את נציג התחבורה {topics[state.pending_rep.reason]}. אעדכן כשתגיע תשובה."


def confirm_group_text(occupation: str, group: str) -> str:
    """שורת אישור לקבוצה שנקבעה לפי התפקיד — מהפרופיל שהכלי שמר."""
    return f"זיהיתי: {occupation}, קבוצה {group}."


def chosen_options_text(labels: list[str]) -> str:
    return f"בחרתם להשוות: {', '.join(labels)}."


# דוגמה מדויקת לכל שדה — מצורפת כשאותה שאלה נשאלת פעם שנייה ברצף (במקום אותו ניסוח)
EXAMPLES = {
    "occupation": "מנהלת מוצר, או: קבוצה ג", "group": "ג", "opt_out_occupation": "מנהלת מוצר",
    "base": "18500", "commissions": "1000, או: אין", "bonus": "500, או: אין",
    "pension_percent": "6", "study_fund_percent": "2.5, או: אין", "credit_points": "2.75",
    "children": "2, או: אין", "car": "קורולה קרוס 2023, או: אין רכב",
    "use_value": "4000, או: לא ידוע", "drive": "חשמלי", "copay": "300, או: לא ידוע",
    "maintenance": "1500", "car_choice": "1", "options": "אטו 3, או: הכי משתלם",
}
_EXAMPLE_NAMES = {"commissions": "עמלות", "bonus": "בונוס", "pension_percent": "פנסיה",
                  "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי", "children": "ילדים",
                  "copay": "השתתפות", "options": "חלופה"}


def retry_hint(fields: list[str]) -> str:
    """שאלה שחוזרת פעם שנייה: לא באותו ניסוח — כפתורי ההצעה, ודוגמה מדויקת לכתיבה."""
    known = [f for f in fields if f in EXAMPLES]
    if not known:
        return "אפשר ללחוץ על אחד הכפתורים."
    example = EXAMPLES[known[0]] if len(known) == 1 else ", ".join(
        f"{_EXAMPLE_NAMES.get(f, f)} {EXAMPLES[f].split(',')[0]}" for f in known[:3])
    return f"אפשר ללחוץ על אחד הכפתורים, או לכתוב. לדוגמה: {example}"


RANK_DEFERRED ="ביקשתם את החלופות הכי משתלמות. אבחר אותן ואחשב ברגע שיהיו לי כל הפרטים."
REP_DEFERRED = "רשמתי שזה לא ידוע. אשאל את נציג התחבורה אחרי שנשלים את שאר הפרטים."


def confirm_car_text(state: ConversationState) -> str:
    """שורת אישור לרכב שזוהה — רק מתוצאת ההתאמה: דגם, קבוצה (לרכב מהקטלוג) והנעה."""
    c = state.current
    details = [c.label or ""]
    if c.group:
        details.append(f"קבוצה {c.group}")
    if c.drive:
        details.append(DRIVE_HE[c.drive.value])
    return f"זיהיתי: {', '.join(details)}. אם זה לא הרכב שלכם, אפשר לתקן."




# ---------- the reply plan (DECIDE) and its template rendering ----------

@dataclass
class ReplyPlan:
    """What the reply must say, decided in code. SAY (the LLM) phrases it; the template renders it
    when the LLM is off, fails or breaks the number guard. Every item comes from the FAQ, the tools'
    results or the state machine."""

    answers: list[str] = field(default_factory=list)     # FAQ answers, one per question asked
    terms: list[str] = field(default_factory=list)       # words a phrased reply must contain (FAQ terms)
    ack: list[str] = field(default_factory=list)         # what was saved / identified this turn
    follow_up: list[str] = field(default_factory=list)   # "כתבתם שיש לכם עמלות. כמה בחודש?"
    ask: list[str] = field(default_factory=list)         # one clarifying question, or the next question
    cta: str = ""                                        # exactly one call to action, the last line
    fixed: bool = False                                  # results / waiting: final text, never rephrased

    def text(self) -> str:
        parts = [x for x in [*self.answers, *self.ack, *self.follow_up, *self.ask, self.cta] if x]
        # safety net only (the root cause is handled where the parts are built): never emit the same
        # block twice in one reply. Order is preserved; the first occurrence wins.
        seen: set[str] = set()
        unique = [p for p in parts if not (p.strip() in seen or seen.add(p.strip()))]
        return "\n\n".join(unique)

    def as_json(self) -> dict[str, Any]:
        return {"answers": self.answers, "ack": self.ack, "follow_up": self.follow_up, "ask": self.ask,
                "cta": self.cta}


CTA_STARTS = ("כתבו", "בחרו", "אפשר לכתוב")
NEXT_AFTER_RESULTS = "רוצים לראות מה הכי משתלם מכל הרכבים שאתם זכאים להם?"
NEXT_AFTER_RANK = "רוצים להשוות רכבים אחרים? כתבו את השמות שלהם."
LINK_LABELS = {"profile_form": "למלא בטופס", "selection": "להציג את הרכבים בקבוצה שלכם",
               "payslip_upload": "להעלות תלוש"}


def _names(fields: Sequence[str]) -> str:
    names = [_LEFTOVER_NAMES[f] for f in fields if f in _LEFTOVER_NAMES]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " ו" + names[-1]


def unknown_text(state: ConversationState, snapshot: RulesSnapshot, fields: Sequence[str]) -> str:
    """Only fields the employee does not know are left: no calculation, never a default. Said once with
    where to find each value; asked again ("לא יודע") in other words, never the same text twice in a row."""
    names = _names(fields)
    if state.unknown_told % 2:
        return f"בסדר. החישוב מחכה רק ל{names}, בלי ערך משוער. אפשר לחזור לכאן בכל זמן ולכתוב את הערך."
    these = "הערך הזה" if len(fields) == 1 else "הערכים האלה"
    lines = [f"כדי לחשב חסר לי: {names}. בלי {these} אין חישוב, וגם לא חישוב עם ערך משוער."]
    where = [f for f in fields if (e := snapshot.faq.get(f)) is not None and e.where]
    if len(where) <= 2:           # where on the payslip, field by field — short
        lines += [f"{_LEFTOVER_NAMES[f]}: {snapshot.faq.get(f).where}" for f in where]
    else:
        lines.append("כולם מופיעים בתלוש השכר החודשי.")
    return "\n".join(lines)
UNANSWERED = "על זה אין לי מידע מדויק. התשובה של נציג התחבורה תגיע לכאן. רוצים שאשאל אותו?"
# the offer is the turn's one call to action; the next question comes after the choice
REP_OFFER_CTA = "בחרו \"לשאול את הנציג\" או \"להמשיך בלי\"."
NOT_UNDERSTOOD = "לא הצלחתי להבין את התשובה."
# examples are built only from fields that are still missing (never "פנסיה 6%" when the pension is known)
_FIELD_EXAMPLE = {"commissions": "אין עמלות", "bonus": "בונוס 2,000", "pension_percent": "פנסיה 6%",
                  "study_fund_percent": "קרן השתלמות 2.5%", "credit_points": "2.25 נקודות זיכוי",
                  "children": "2 ילדים"}
FIELD_NAMES = {"occupation": "קבוצה או תפקיד", "group": "קבוצת הזכאות", "opt_out_occupation": "התפקיד",
               "base": "שכר בסיס", "commissions": "עמלות", "bonus": "בונוס", "pension_percent": "פנסיה",
               "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי", "children": "ילדים",
               "car": "הרכב שלכם היום", "copay": "ההשתתפות שלכם ברכב", "use_value": "שווי השימוש",
               "drive": "סוג ההנעה", "maintenance": "אחזקת הרכב", "car_choice": "גרסת הרכב",
               "options": "הרכבים להשוואה"}


def q2_example(fields: Sequence[str]) -> str:
    return ", ".join([_FIELD_EXAMPLE[f] for f in fields if f in _FIELD_EXAMPLE][:3])


def _q1_example(fields: Sequence[str]) -> str:
    asks_group = any(f in ("occupation", "group") for f in fields)
    if asks_group and "base" in fields:
        return "קבוצה ג, שכר בסיס 18,500"
    return "קבוצה ג, או: מנהלת מוצר" if asks_group else "18,500"


def _list(names: Sequence[str]) -> str:
    names = list(dict.fromkeys(names))
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " ו" + names[-1]


def missing_names(state: ConversationState) -> list[str]:
    """What is still needed before a calculation, in the order it is asked."""
    from .stage import missing_current_car, open_profile, unknown_missing

    p = state.profile
    fields = [*open_profile(p), *missing_current_car(state.current), *unknown_missing(p)]
    if not state.selected and not state.rank_requested:
        fields.append("options")
    return [FIELD_NAMES[f] for f in fields if f in FIELD_NAMES]


def compare_needs_text(state: ConversationState) -> str:
    """A request the bot cannot do yet ("תשווה ביניהם" before the profile is complete): what is needed."""
    names = missing_names(state)
    return f"כדי להשוות, חסרים לי עוד: {_list(names)}." if names else ""


ALL_AT_ONCE = "אני שואל בשלבים כדי שלא יחסר דבר, אבל אפשר לענות על הכול בהודעה אחת."

_PROFILE_ARGS = (("base_salary", "base"), ("commissions", "commissions"), ("bonus", "bonus"),
                 ("pension_percent", "pension_percent"), ("study_fund_percent", "study_fund_percent"),
                 ("credit_points", "credit_points"), ("children_under_23", "children"))


def progress_text(state: ConversationState, fields: Sequence[str]) -> str:
    """The same compound question again: what is already saved, then only what is still missing
    ("רשמתי: שכר בסיס 18,500 ₪, פנסיה 5%. עדיין חסרים לי: עמלות, בונוס…"), never the full question verbatim."""
    from .fallback import recorded_text

    p = state.profile
    saved = {arg: getattr(p, attr) for arg, attr in _PROFILE_ARGS if getattr(p, attr) is not None}
    missing = still_missing_text(paced(state, fields))
    return f"רשמתי: {recorded_text(saved)}. {missing}" if saved else missing


def still_missing_text(fields: Sequence[str]) -> str:
    names = [FIELD_NAMES[f] for f in fields if f in FIELD_NAMES]
    if not names:
        return ""
    return f"עדיין חסר לי: {names[0]}." if len(names) == 1 else f"עדיין חסרים לי: {_list(names)}."
_PRESENT_NAMES = {"commissions": "עמלות", "bonus": "בונוס", "pension_percent": "הפרשה לפנסיה",
                  "study_fund_percent": "קרן השתלמות", "children": "ילדים"}
_Q2_SIMPLE = ("במילים פשוטות: אני צריך לדעת כמה כסף נוסף על הבסיס מקבלים בחודש, כמה אחוזים יורדים "
              "לפנסיה ולקרן השתלמות, וכמה נקודות זיכוי יש לכם.")


def _faq_example(snapshot: RulesSnapshot, key: str) -> str:
    entry = snapshot.faq.get(key)
    return entry.example if entry is not None and entry.example else EXAMPLES.get(key, "").split(", ")[0]


def _is_q2(fields: Sequence[str]) -> bool:
    return bool(fields) and all(f in Q2_FIELDS for f in fields)


def call_to_action(state: ConversationState, snapshot: RulesSnapshot,
                   fields: Optional[Sequence[str]] = None) -> tuple[str, Optional[str]]:
    """The one call to action that ends a reply, and the one link it names (or None)."""
    if state.pending_rep_question is not None:
        return "בחרו \"לשלוח\" או \"לבטל\".", None
    if state.unanswered_question is not None:
        return REP_OFFER_CTA, None
    if state.pending_ambiguity is not None:
        return "בחרו אחת מהאפשרויות, או כתבו \"אין רכב\" או את דגם הרכב.", None
    if state.pending_confirmation is not None:
        if state.pending_confirmation.unusual:
            return "בחרו \"לאשר\" או \"לתקן\", או כתבו את הערך הנכון.", None
        if state.pending_confirmation.scale:
            return "בחרו אחד מהסכומים, או כתבו את הסכום המלא.", None
        return "בחרו \"כן\" או \"לא\", או כתבו את הסכום המלא.", None
    if state.pending_occupation is not None:
        return "בחרו תפקיד מהרשימה, או כתבו את התפקיד שלכם.", None
    if state.pending_clarification is not None:
        return "בחרו או כתבו: \"הרכב שלי\" או \"חלופה\".", None
    group = current_group(state)[0]
    fields = paced(state, current_group(state)[1] if fields is None else fields)
    if not fields:
        return "", None
    if group == "unknown":
        ext = snapshot.extensions
        link = "payslip_upload" if (ext.payslip_upload_enabled or ext.payslip_upload_url) else None
        example = _faq_example(snapshot, fields[0]).split(", ")[0]
        return (f"כתבו את הערך כשתמצאו אותו בתלוש, למשל: \"{example}\"" + (", או העלו את התלוש." if link else "."),
                link)
    # the field the question text asks (group_question's order), not just the first open one
    first = next((f for f in ("car", "copay", "opt_out_occupation", "options") if f in fields), fields[0])
    if _is_q2(fields):
        example = q2_example(fields) if len(fields) > 1 else _faq_example(snapshot, first)
        ext = snapshot.extensions
        # "all at once" (state.link_now set by the turn) wants the form, to fill everything together.
        # Otherwise, when the built-in uploader is enabled, offer it (once) instead of the manual form link,
        # so the employee can upload rather than type. Only one Q2 link ever.
        if state.link_now != "profile_form" and (ext.payslip_upload_enabled or ext.payslip_upload_url):
            link = _once(state, "payslip_upload")
            return f"אפשר לכתוב כאן, למשל: \"{example}\"" + (", או להעלות תלוש." if link else "."), link
        link = _once(state, "profile_form")
        return f"אפשר לכתוב כאן, למשל: \"{example}\"" + (", או למלא בטופס." if link else "."), link
    if fields == ["group"]:
        return "בחרו קבוצה, או כתבו אותה, למשל: ג.", None
    if fields == ["base"]:
        return f"כתבו את הסכום, למשל: {_faq_example(snapshot, 'base').split()[-1]}.", None
    if first in ("occupation", "group", "base"):
        return f"כתבו כאן, למשל: \"{_q1_example(fields)}\".", None
    if first == "opt_out_occupation":
        return "כתבו את התפקיד, למשל: מנהלת מוצר.", None
    if first == "car":
        if "copay" in fields:
            return (f"כתבו את הדגם וכמה משלמים, למשל: \"{_faq_example(snapshot, 'car')}\". "
                    "אם אין רכב, כתבו \"אין רכב\"."), None
        return "כתבו את הדגם, למשל: \"קורולה קרוס 2025\". אם אין רכב, כתבו \"אין רכב\".", None
    if first == "copay":
        return "כתבו את הסכום, למשל: 300, או \"לא ידוע\".", None
    if first == "use_value":
        return "כתבו את הסכום מהתלוש, למשל: 4,000, או \"לא ידוע\".", None
    if first == "drive":
        return "בחרו או כתבו, למשל: היברידי.", None
    if first == "maintenance":
        return "כתבו את הסכום מהתלוש, למשל: 1,500.", None
    if first == "car_choice":
        return "בחרו גרסה, או כתבו את המספר שלה.", None
    if first == "options":
        return (f"כתבו שמות של עד {MAX_OPTIONS} רכבים, למשל: \"{_faq_example(snapshot, 'options').split(',')[0]}\", "
                "או \"הכי משתלם\"."), _once(state, "selection")
    return "כתבו כאן את התשובה.", None


def placeholder_text(state: ConversationState, snapshot: RulesSnapshot) -> str:
    """The composer hint: mirrors the question on screen with an example."""
    if state.pending_rep_question is not None:
        return "למשל: לשלוח"
    if state.unanswered_question is not None:
        return "למשל: לשאול את הנציג"
    if state.pending_ambiguity is not None:
        return "למשל: אין רכב"
    if state.pending_confirmation is not None or state.pending_clarification is not None:
        return "למשל: כן"
    if state.pending_occupation is not None:
        return "למשל: מנהלת מוצר"
    if next_stage(state) == Stage.WAITING_FOR_REP:
        return "אפשר לשאול שאלה בזמן ההמתנה"
    fields = current_group(state)[1]
    if not fields:
        return "למשל: מה זה שווי שימוש?"
    first = fields[0]
    if _is_q2(fields):
        return "למשל: " + (q2_example(fields) if len(fields) > 1 else _faq_example(snapshot, first))
    if first in ("occupation", "group", "base"):
        return "למשל: " + _q1_example(fields)
    key = {"opt_out_occupation": "occupation"}.get(first, first)
    return "למשל: " + (_faq_example(snapshot, key) or EXAMPLES.get(first, ""))


def explain_text(state: ConversationState, snapshot: RulesSnapshot, fields: Sequence[str]) -> str:
    """"מה?" / the same question again: the question in simpler words (never a verbatim repeat)."""
    if not fields:
        return ""
    group = current_group(state)[0]
    first = next((f for f in ("car", "copay", "opt_out_occupation", "options") if f in fields), fields[0])
    if _is_q2(fields) and len(fields) > 1:
        return _Q2_SIMPLE
    key = "occupation" if group == "Q1" else "car" if first in ("car", "copay") and "car" in fields else first
    entry = snapshot.faq.get(key)
    if entry is not None and entry.simple:
        return f"במילים פשוטות: {entry.simple}"
    return f"במילים פשוטות: {question(first, state, snapshot)}" if first != "options" else (
        "במילים פשוטות: אילו רכבים לבדוק במקום הרכב של היום.")


def follow_up_text(fields: Sequence[str], snapshot: RulesSnapshot) -> str:
    """A field said to exist without an amount: ask the amount (never saved as 0)."""
    names = [_PRESENT_NAMES[f] for f in fields if f in _PRESENT_NAMES]
    if not names:
        return ""
    said = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " ו" + names[-1]
    if all(f in ("commissions", "bonus") for f in fields):
        ask = "כמה בחודש? גם הערכה מספיקה." if len(fields) == 1 else "כמה כל אחד בחודש? גם הערכה מספיקה."
    elif list(fields) == ["children"]:
        ask = f"כמה ילדים עד גיל {snapshot.strauss.family_track.child_max_age}?"
    else:
        ask = "מה הסכום או האחוז של כל אחד?"
    return f"כתבתם שיש לכם {said}. {ask}"


def options_ask(state: ConversationState, snapshot: RulesSnapshot) -> str:
    cars = [o for o in available_options(snapshot, state) if o.car is not None]
    group = state.profile.group
    if not group or not cars:
        return OPTIONS_ASK
    return f"איזה רכבים להשוות? בקבוצה {group} ומטה יש {len(cars)} רכבים, ואפשר לבחור עד {MAX_OPTIONS}."


def plan_reply(state: ConversationState, snapshot: RulesSnapshot, *, understood: bool,
               just_calculated: bool, matched_car: bool = False, notice: Optional[str] = None,
               rep_opened: bool = False, notice_asks: bool = False, confirmations: Sequence[str] = (),
               answers: Sequence[tuple[str, str]] = (), follow_up: Sequence[str] = (),
               explain: bool = False, extra: Sequence[str] = ()) -> ReplyPlan:
    """The next reply, from the state only. answers: (FAQ answer, term) pairs; extra: lines that come
    from code (an unanswered question, a redirect) and go right after the answers."""
    plan = ReplyPlan(answers=[a for a, _ in answers] + list(extra), terms=[t for _, t in answers],
                     ack=[*confirmations])
    if matched_car:
        plan.ack.append(confirm_car_text(state))
    if notice and not notice_asks:
        plan.ack.append(notice)
    waiting = state.stage == Stage.WAITING_FOR_REP
    # השתתפות שהנציג חייב לאשר (REP_REQUIRED): התוצאה מוצגת רק אחרי התשובה
    hide_results = waiting and state.pending_rep is not None and state.pending_rep.reason == RepReason.NEW_CAR_COPAY
    if just_calculated and state.results and not hide_results:
        plan.ack.append(results_text(state))
        plan.fixed = True
    if waiting:
        plan.ask.append(asked_text(state) if rep_opened else WAITING)
        plan.fixed = True
        return plan
    if plan.fixed and state.stage == Stage.PRESENTED:
        # after results: why (the engine's main driver, in words), then one relevant next step
        ok = [r for r in state.results if r.result.get("status") == "OK" and r.result.get("delta_display") is not None]
        if ok:
            best = max(ok, key=lambda r: Decimal(str(r.result["delta_display"])))
            if best.result.get("why"):
                plan.ask.append(f"הסיבה העיקרית ב-{best.label}: {best.result['why']}")
            plan.cta = (NEXT_AFTER_RANK if state.ranked_count else NEXT_AFTER_RESULTS)
        return plan
    cta, _ = call_to_action(state, snapshot)
    plan.cta = cta
    if state.pending_rep_question is not None:
        plan.ask.append(f"אשלח לנציג התחבורה: \"{state.pending_rep_question}\". לשלוח?")
        return plan
    if state.unanswered_question is not None:
        plan.ask.append(UNANSWERED)            # one call to action: the offer (its buttons); no flow question
        plan.cta = ""
        return plan
    if state.pending_ambiguity is not None:
        plan.ask.append("לא בטוח שהבנתי: אין לכם רכב צמוד היום, או שיש רכב וההשתתפות שלכם היא 0?")
        return plan
    if state.pending_clarification is not None:
        plan.ask.append(clarify_role_text(state.pending_clarification.label))
        return plan
    fields = current_group(state)[1]
    if notice_asks and notice:
        plan.ask.append(notice)                        # range problem / conflict: the sentence asks
        return plan
    if state.pending_confirmation is not None:
        pending = state.pending_confirmation
        plan.ask.append(confirm_value_text(pending.field, pending.value, pending.unusual, pending.scale))
        return plan
    if state.pending_occupation is not None:
        plan.ask.append(confirm_occupation_text(state.pending_occupation.text,
                                                state.pending_occupation.suggestions))
        return plan
    asked_something = bool(answers or extra or follow_up)
    repeat = not understood and not asked_something and fields == state.last_asked
    if not understood and not asked_something and not explain:
        plan.ask.append(CAR_AGAIN if fields[:1] == ["car"] else NOT_UNDERSTOOD)
    present = [f for f in follow_up if f in fields]
    if present:
        plan.follow_up.append(follow_up_text(present, snapshot))
    rest = [f for f in fields if f not in present]
    if explain or repeat:
        compound = len(paced(state, fields)) > 1 and _is_q2(fields)
        line = (progress_text(state, fields) if repeat and not explain and compound
                else explain_text(state, snapshot, fields))
        # root cause of the duplicate bubble: the parser's notice (already in plan.ack) can be the same
        # progress line; do not add it a second time to plan.ask
        if line and line not in plan.ack:
            plan.ask.append(line)
        if repeat and state.misses >= 1:              # missed again: never the same reply twice
            one = placeholder_text(state, snapshot).removeprefix("למשל: ").split(", ")[0]
            plan.ask.append(f"אפשר גם לענות על דבר אחד בכל פעם, למשל: {one}.")
    elif rest:
        plan.ask.append(group_question(state, snapshot, skip=present))
    plan.ask = [a for a in plan.ask if a]
    _vary_repeat(plan, state, snapshot, fields)
    return plan


def _vary_repeat(plan: ReplyPlan, state: ConversationState, snapshot: RulesSnapshot, fields: Sequence[str]) -> None:
    """The same question is never asked verbatim twice in a row: narrowed to what is still missing, or
    in simpler words."""
    if not plan.ask or plan.ask[-1] != state.last_ask:
        return
    first = progress_text(state, fields) if len(paced(state, fields)) > 1 else still_missing_text(fields)
    for other in (first, explain_text(state, snapshot, fields)):
        if other and other != state.last_ask:
            plan.ask[-1] = other
            return
    plan.ask[-1] = "שאלה אחת לפני שממשיכים: " + plan.ask[-1]


def render(state: ConversationState, snapshot: RulesSnapshot, **kwargs: Any) -> str:
    """ההודעה הבאה כטקסט תבנית (בלי מודל השפה)."""
    return plan_reply(state, snapshot, **kwargs).text()


def unknown_ack_text(fields: Sequence[str]) -> str:
    """"לא יודע" recorded per field: said once, with what it means (no default, asked again at the end)."""
    return f"רשמתי שזה לא ידוע: {_names(fields)}. בלי ערך משוער; נחזור לזה בסוף."


_SINGULAR_TO_PLURAL = [(re.compile(rf"(?<![א-ת]){a}(?![א-ת])"), b) for a, b in
                       (("שלך", "שלכם"), ("לך", "לכם"), ("ממך", "מכם"), ("בחרת", "בחרתם"))]


def to_plural(text: str) -> str:
    """Engine texts keep the reference wording (singular); the employee always reads plural."""
    for pattern, plural in _SINGULAR_TO_PLURAL:
        text = pattern.sub(plural, text)
    return text
