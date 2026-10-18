"""הודעות תבנית בעברית. ניסוח ניטרלי מגדרית: רבים ("כתבו") או צורות בלי מגדר — אף פעם לא מניחים מגדר. משמשות במצב גיבוי, כטיוטה למודל השפה, וכתחליף כשהשומר חוסם.

כל מספר כאן מגיע מתוצאת המנוע, מהפרופיל או מנתוני ההגדרות — לא מחושב כאן.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.modules.rules.public import RulesSnapshot
from app.shared.money import to_shekel

from .stage import MAX_OPTIONS, ConversationState, RepReason, Stage, current_group
from .tools import available_options

DRIVE_HE = {"PETROL": "בנזין", "HYBRID": "היברידי", "PLUG_IN": "פלאג-אין", "ELECTRIC": "חשמלי"}


def money(value: Any) -> str:
    return f"{to_shekel(Decimal(str(value))):,} ₪"


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
    return f"מה קבוצת הזכאות שלך ({order[0]}-{order[-1]}), או מה התפקיד שלך"


Q3_CAR = "מה הרכב היום (דגם ושנת רישום) ומה ההשתתפות שלך בו?"
_Q2_PAY = {"commissions": "עמלות", "bonus": "בונוסים"}
_Q2_DEDUCTIONS = {"pension_percent": "פנסיה", "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי"}
# שדה מ-Q2 שנשאר חסר ונשאל בתוך Q3
_LEFTOVER = {"commissions": "יש לך עמלות חודשיות?", "bonus": "יש לך בונוס חודשי?",
             "pension_percent": "כמה אחוזים מופרשים ממך לפנסיה?",
             "study_fund_percent": "כמה אחוזים מופרשים ממך לקרן השתלמות?",
             "credit_points": "כמה נקודות זיכוי יש לך?"}


_LEFTOVER_NAMES = {"commissions": "עמלות", "bonus": "בונוס", "pension_percent": "פנסיה",
                   "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי", "children": "ילדים"}


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


def _leftover_asks(fields: list[str], snapshot: RulesSnapshot) -> list[str]:
    age = snapshot.strauss.family_track.child_max_age
    asks = [_LEFTOVER[f] for f in fields if f in _LEFTOVER]
    if "children" in fields:
        asks.append(f"כמה ילדים עד גיל {age} יש לך?")
    return asks


def _leftover_tail(fields: list[str]) -> str:
    """Q3's first message names what is still missing from Q2; the client draws the "השלמה" button."""
    return "חסר עוד: " + ", ".join(_LEFTOVER_NAMES[f] for f in fields if f in _LEFTOVER_NAMES) + "."


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
        lines = [*first, OPTIONS_ASK, *_options_block(state, snapshot, False)]
    else:
        lines = list(first)
    # מה שנשאר מ-Q2: כזנב רק בפעם הראשונה ש-Q3 מוצגת; אחר כך לא חוזר, ונשאל לבד כשאין שאלה אחרת
    asks = _leftover_asks(fields, snapshot)
    if asks and not lines:
        lines.append(" ".join(asks))
    elif asks and "Q3" not in state.asked_groups:
        lines.append(_leftover_tail(fields))
    return "\n".join(lines)


def net_change(delta: Any) -> str:
    """"הנטו שלך יגדל ב-250 ₪ בחודש". The same wording everywhere: future tense, never "ירד"/"עלה"."""
    v = to_shekel(Decimal(str(delta)))
    if v == 0:
        return "הנטו שלך לא ישתנה"
    return f"הנטו שלך {'יגדל' if v > 0 else 'יקטן'} ב-{money(abs(v))} בחודש"


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


def pending_copay_text(label: str, amount: Any) -> str:
    return "ממתין לאישור נציג על ההשתתפות."


def results_text(state: ConversationState) -> str:
    """At most two short lines for a plain comparison: a summary, then what is waiting. The detail
    lives only in the structured results (the summary card and the comparison panel)."""
    ok, bad = [], []
    for item in state.results:
        r = item.result
        (ok if r["status"] == "OK" and r.get("delta_display") is not None else bad).append(item)
    lines: list[str] = []
    if len(ok) == 1:
        lines.append(f"בדקתי את {ok[0].label}. {net_change(ok[0].result['delta_display'])}.")
    elif ok:
        deltas = [Decimal(str(i.result["delta_display"])) for i in ok]
        best = max(ok, key=lambda i: Decimal(str(i.result["delta_display"])))
        head = f"בדקתי {len(ok)} רכבים."
        if all(d > 0 for d in deltas):
            lines.append(f"{head} בכולם הנטו שלך יגדל, הכי הרבה ב-{best.label}. {net_change(best.result['delta_display'])}.")
        elif all(d < 0 for d in deltas):
            lines.append(f"{head} בכולם הנטו שלך יקטן, הכי פחות ב-{best.label}. {net_change(best.result['delta_display'])}.")
        elif all(d == 0 for d in deltas):
            lines.append(f"{head} בכולם הנטו שלך לא ישתנה.")
        else:
            lines.append(f"{head} הכי משתלם {best.label}. {net_change(best.result['delta_display'])}.")
    for item in bad:
        r = item.result
        if r["status"] == "NOT_ELIGIBLE":
            lines.append(f"{item.label} לא זמין לך: {r['reason']}")
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


_UNUSUAL_ASK = {
    "pension_percent": ("לאשר פנסיה של {v}?", "רוב השכירים מפרישים לפנסיה."),
    "credit_points": ("לאשר {v} נקודות זיכוי?", "רוב השכירים מקבלים נקודות זיכוי."),
    "base_salary": ("לאשר שכר בסיס של {v}?", "זה נמוך מהמקובל."),
    "children_under_23": ("לאשר {v} ילדים?", "זה מספר גדול של ילדים."),
}


def unusual_fix_text(field: str) -> str:
    """"לתקן" on a rare value: the field stays open and is asked again."""
    return f"בסדר. {_RANGE_TEXT[field][1]}"


def confirm_value_text(field: str, value: Decimal, unusual: bool = False) -> str:
    if unusual:
        ask, note = _UNUSUAL_ASK.get(field, (f"לאשר {_VALUE_LABELS[field][0]} {{v}}?", "הערך נדיר."))
        return f"{ask.format(v=_show(field, value))} {note}"
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


def rep_changed_text(label: str, amount: Decimal, effective: Optional[date], new_delta: Any) -> str:
    """Numbers from the answer and the engine result only."""
    return (f"נציג התחבורה עדכן את ההשתתפות ב-{label} ל-{money(amount)}{_effective(effective)}.\n"
            f"עכשיו {net_change(new_delta)}.")


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


def confirm_group_text(occupation: str, group: str) -> str:
    """שורת אישור לקבוצה שנקבעה לפי התפקיד — מהפרופיל שהכלי שמר."""
    return f"זיהיתי: {occupation}, קבוצה {group}."


def chosen_options_text(labels: list[str]) -> str:
    return f"בחרת להשוות: {', '.join(labels)}."


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


RANK_DEFERRED ="ביקשת את החלופות הכי משתלמות. אבחר אותן ואחשב ברגע שיהיו לי כל הפרטים."
REP_DEFERRED = "רשמתי שזה לא ידוע. אשאל את נציג התחבורה אחרי שנשלים את שאר הפרטים."


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
           rep_opened: bool = False, notice_asks: bool = False, confirmations: Sequence[str] = ()) -> str:
    """ההודעה הבאה לפי מצב השיחה בלבד (שורות אישור מתוצאות הכלים, ומשפט מהקורא, לפני השאלה)."""
    parts: list[str] = [*confirmations]
    if matched_car:
        parts.append(confirm_car_text(state))
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
        if fields == state.last_asked:               # פעם שנייה: גם כפתורים ודוגמה, לא אותה הודעה
            parts.append(retry_hint(fields))
        return "\n\n".join(parts)
    if notice_asks:
        return "\n\n".join(parts)                       # המשפט כבר שואל (בעיית סבירות)
    if state.pending_confirmation is not None:
        pending = state.pending_confirmation
        parts.append(confirm_value_text(pending.field, pending.value, pending.unusual))
        return "\n\n".join(parts)
    if state.pending_occupation is not None:
        parts.append(confirm_occupation_text(state.pending_occupation.text, state.pending_occupation.suggestions))
        return "\n\n".join(parts)
    if not understood:
        parts.append("לא הצלחתי להבין את התשובה.")
    if state.stage == Stage.PRESENTED and just_calculated:
        pass                      # the results card carries "לשנות בחירה"; the text stays at two lines
    elif fields:
        # אותה שאלה פעם שנייה ברצף: לא באותו ניסוח, אלא כפתורים ודוגמה מדויקת
        parts.append(group_question(state, snapshot))
        if not understood and fields == state.last_asked:
            parts[-1] += "\n" + retry_hint(fields)   # אותה שאלה פעם שנייה: עוד כפתורים ודוגמה, לא אותה הודעה
    return "\n\n".join(parts)
