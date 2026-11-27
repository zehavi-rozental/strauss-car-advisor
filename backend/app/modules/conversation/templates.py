"""הודעות תבנית בעברית. ניסוח ניטרלי מגדרית: רבים ("כתבו") או צורות בלי מגדר — אף פעם לא מניחים מגדר. משמשות במצב גיבוי, כטיוטה למודל השפה, וכתחליף כשהשומר חוסם.

כל מספר כאן מגיע מתוצאת המנוע, מהפרופיל או מנתוני ההגדרות — לא מחושב כאן.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.modules.rules.public import FAMILY_GROUP, RulesSnapshot
from app.shared.money import to_shekel

from .actions import payslip_allowed
from .stage import (ESTIMABLE, MAX_OPTIONS, PROFILE_ARGS, Q2_FIELDS, REP_ASKABLE, profile_args, ConversationState, RepReason,
                    Stage, current_group, current_unknown, missing_current_car, next_stage, open_profile, results_stale,
                    unknown_missing, with_rep)
from .tools import available_options

DRIVE_HE = {"PETROL": "בנזין", "HYBRID": "היברידי", "PLUG_IN": "פלאג-אין", "ELECTRIC": "חשמלי"}


def signed(value: Any) -> str:
    """"+611 ₪" / "-50 ₪" / "0 ₪": the sign is explicit; the client shows the minus as "−"."""
    v = to_shekel(Decimal(str(value)))
    return f"{v:+,} ₪" if v != 0 else "0 ₪"


def money(value: Any) -> str:
    return f"{to_shekel(Decimal(str(value))):,} ₪"


_LABEL_ORDER = ("base_salary", "commissions", "bonus", "pension_percent", "study_fund_percent",
                "credit_points", "children_under_23")


def recorded_text(labelled: dict[str, object]) -> str:
    """"אין עמלות, פנסיה 6%, 2 ילדים" — הערכים כפי שנרשמו (המספרים מההודעה), בסדר קבוע."""
    def text(f: str, v: object) -> str:
        zero = v == 0
        return {
            "base_salary": lambda: f"שכר בסיס {money(v)}",
            "commissions": lambda: "אין עמלות" if zero else f"עמלות {money(v)}",
            "bonus": lambda: "אין בונוס" if zero else f"בונוס {money(v)}",
            "pension_percent": lambda: f"פנסיה {Decimal(str(v)).normalize():f}%",
            "study_fund_percent": lambda: "אין קרן השתלמות" if zero else f"קרן השתלמות {Decimal(str(v)).normalize():f}%",
            "credit_points": lambda: f"{Decimal(str(v)).normalize():f} נקודות זיכוי",
            "children_under_23": lambda: "אין ילדים" if zero else "ילד אחד" if v == 1 else f"{v} ילדים",
        }[f]()

    return ", ".join(text(f, labelled[f]) for f in _LABEL_ORDER if f in labelled)


CAR_AGAIN = "לא זיהיתי את דגם הרכב."

GREETING = ("שלום! אני יועץ הרכב של שטראוס.\n"
            "אעזור לכם להבין אילו רכבים מתאימים לכם ואיך כל בחירה משפיעה על הנטו שלכם.\n"
            "כדי להתחיל, אשמח לדעת: מה קבוצת הזכאות שלכם (א–ד)? האם יש לכם היום רכב חברה? "
            "והאם יש רכב או סוג רכב שמעניין אתכם — חשמלי, היברידי או משפחתי?\n"
            "אפשר לענות על הכול יחד או בהדרגה, ואת פרטי השכר נשלים בהמשך.")


def catalog_copay_hint(state: ConversationState, snapshot: RulesSnapshot) -> Optional[Any]:
    """The catalog's default copay for today's car (31/10), when today's car is a catalog car and its copay is not
    known. Only a suggestion: the catalog amount is a general default ("דמה" in the data file), not the employee's
    agreement — it is shown as unverified and saved only when the employee confirms it."""
    c = state.current
    if c.source != "catalog" or not c.catalog_code or c.copay is not None:
        return None
    car = next((x for x in snapshot.catalog if x.catalog_code == c.catalog_code), None)
    return car.default_copay if car is not None else None


def copay_choices_offered(state: ConversationState, snapshot: RulesSnapshot) -> bool:
    """Today's car is known and its copay is asked (1/11): the copay is a closed question — three choices with a
    catalog amount, else "להזין סכום" / "לברר מול נציג" (nothing is invented when the catalog has no amount)."""
    c = state.current
    return (c.source in ("catalog", "tax", "stated") and c.copay is None and not c.copay_unknown
            and c.copay_provisional is None)


_NOT_ON_PAYSLIP = ("children",)     # faq.yaml children.where: "זה לא בתלוש"


def maintenance_question(state: ConversationState) -> str:
    """No company car today: the car-maintenance allowance, a closed question like today's copay (19/11). An amount read
    from a payslip the employee reviewed is offered to confirm, change or check with the representative; without one
    nothing is suggested: an amount, "אין אחזקת רכב" or the representative. Never assumed, never 0 by itself."""
    if state.payslip_maintenance is not None:
        return (f"מכיוון שאין לכם רכב צמוד: בתלוש שהעליתם זיהיתי אחזקת רכב של {money(state.payslip_maintenance)} "
                "בחודש. זה הסכום שאתם מקבלים היום?")
    if state.payslip_reviewed:
        return ("מכיוון שאין לכם רכב צמוד, האם אתם מקבלים היום אחזקת רכב? בתלוש שהעליתם לא זיהיתי שורה כזו, "
                "ולכן לא אניח סכום.")
    return "מכיוון שאין לכם רכב צמוד, האם אתם מקבלים היום אחזקת רכב בתלוש? אם כן, כמה בחודש?"


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
        "car": "מה הדגם של הרכב הצמוד שלכם?" if c.has_car else "איזה רכב צמוד יש לכם היום?",
        "use_value": (f"מאגר רשות המסים כולל רכבים משנות {c.coverage}, ושנת {c.reg_year} לא נמצאת בו. "
                      "מה שווי השימוש החודשי שמופיע בתלוש שלכם?") if c.coverage else
                     f"לא מצאתי את \"{c.query or ''}\" במאגר. מה שווי השימוש החודשי שמופיע בתלוש שלכם?",
        "drive": "מה סוג ההנעה של הרכב: בנזין, היברידי, פלאג-אין או חשמלי?",
        "copay": "מה ההשתתפות החודשית שלכם היום בעלות הרכב?",
        "maintenance": maintenance_question(state),
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
    if field == "copay" and (hint := catalog_copay_hint(state, snapshot)) is not None:
        return (f"לפי הקטלוג, ההשתתפות החודשית ב-{c.label} היא {money(hint)}. זה סכום כללי, ובהסכם האישי שלכם "
                "הוא עשוי להיות שונה. מה נכון אצלכם?")
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


def today_cars_text(snapshot: RulesSnapshot, unsure: bool, ask: bool = True) -> str:
    """The catalog's cars, to find today's car in (1/11): names from the catalog only, nothing assumed. `ask`: the
    template's question line (with the model, its reply asks)."""
    labels = sorted({c.label for c in snapshot.catalog})
    intro = "אין בעיה, נזהה אותו יחד. אלה הרכבים בקטלוג של שטראוס:" if unsure else "אלה הרכבים בקטלוג של שטראוס:"
    question = ["איזה מהם הרכב הצמוד שלכם היום? אם הוא לא ברשימה, כתבו את הדגם והשנה."] if ask else []
    return "\n".join([intro, *(f"- {label}" for label in labels), *question])


def options_list_text(state: ConversationState, snapshot: RulesSnapshot, ask: bool = True) -> str:
    """The alternatives, numbered (1/11): the same eligible list and numbers as the selection card."""
    question = [f"איזה מהם להשוות? אפשר לבחור עד {MAX_OPTIONS}."] if ask else []
    return "\n".join(["אלה הרכבים שאתם זכאים להם:", *_option_lines(state, snapshot), *question])


def options_text(state: ConversationState, snapshot: RulesSnapshot, with_list: bool = False) -> str:
    return "\n".join([OPTIONS_ASK, *_options_block(state, snapshot, with_list)])


# ---------- שאלות מקובצות (נוסח התרחיש הרשמי; רק מה שעדיין חסר) ----------

def _group_or_occupation(snapshot: RulesSnapshot) -> str:
    order = snapshot.calc.group_order
    # בתרחיש הרשמי השאלה בצורת לוכסן לשני המגדרים; התבניות לא משתמשות בצורות לוכסן — אותה שאלה, בלי מגדר
    return f"מה קבוצת הזכאות שלכם ({order[0]}-{order[-1]}), או מה התפקיד שלכם"


Q3_CAR = "איזה רכב צמוד יש לכם היום, וכמה משלמים עליו בחודש?"
Q3_CAR_KNOWN = "מה הדגם של הרכב הצמוד שלכם, וכמה משלמים עליו בחודש?"
_Q2_PAY = {"commissions": "עמלות", "bonus": "בונוסים"}
_Q2_DEDUCTIONS = {"pension_percent": "פנסיה", "study_fund_percent": "קרן השתלמות"}
# שדה מ-Q2 שנשאר חסר ונשאל בתוך Q3
_LEFTOVER = {"commissions": "יש לכם עמלות חודשיות?", "bonus": "יש לכם בונוס חודשי?",
             "pension_percent": "כמה אחוזים מופרשים מכם לפנסיה?",
             "study_fund_percent": "כמה אחוזים מופרשים מכם לקרן השתלמות?",
             "credit_points": "כמה נקודות זיכוי יש לכם?"}


_LEFTOVER_NAMES = {"commissions": "עמלות", "bonus": "בונוס", "pension_percent": "פנסיה",
                   "study_fund_percent": "קרן השתלמות", "credit_points": "נקודות זיכוי", "children": "ילדים"}
# what is left when only unknown values remain: the Q2 leftovers, and today's car values the employee does not know
# (rule A, 22/10: asked from the employee, never the representative)
_UNKNOWN_NAMES = {**_LEFTOVER_NAMES, "copay": "ההשתתפות ברכב של היום", "use_value": "שווי השימוש של הרכב של היום",
                  "maintenance": "סכום אחזקת הרכב"}


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


def q2_started(state: ConversationState) -> bool:
    """The salary question was asked, and the employee already answered part of it (a value, or "לא יודע"): what is
    left is asked one item at a time. The first time it is asked it stays one question, naming only what is missing."""
    p = state.profile
    return "Q2" in state.asked_groups and any(getattr(p, f) is not None or f in p.unknown_fields for f in Q2_FIELDS)


def ask_fields(state: ConversationState, group: Optional[str], fields: Sequence[str]) -> list[str]:
    """The fields the question on screen asks — its words and its inputs (expects) alike. After a partial answer to
    the salary questions, what is left is asked one item at a time, in the usual order (16/11). The two contributions
    are one item ("אילו הפרשות יורדות לכם: פנסיה, קרן השתלמות?"), as they always were: "אין הפרשות" / a short "לא"
    to it is one confirmation for both (1/11). "הכל ביחד" (pace all_at_once) keeps the whole question."""
    fields = list(fields)
    if group != "Q2" or len(fields) <= 1 or not q2_started(state) or state.pace == "all_at_once":
        return fields
    first = min(fields, key=lambda f: _ASK_ORDER.index(f) if f in _ASK_ORDER else len(_ASK_ORDER))
    pair = [f for f in ("pension_percent", "study_fund_percent") if f in fields]
    return pair if first in pair else [first]


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
    if group == "unknown":
        # what the calculation waits for is said in full, whatever the pace (16/11: "החישוב מחכה רק לקרן השתלמות"
        # while the credit points were missing too)
        unknown = [f for f in fields if f not in skip]
        return unknown_text(state, snapshot, unknown) if unknown else ""
    fields = paced(state, [f for f in fields if f not in skip])
    if not fields:
        return ""
    fields = ask_fields(state, group, fields)
    if len(fields) == 1:
        return question(fields[0], state, snapshot)
    if group == "Q1":
        return _q1(fields, state, snapshot)
    if group == "unknown":
        return unknown_text(state, snapshot, fields)
    if group == "Q2" or _is_q2(fields):
        return _q2(fields, snapshot)
    if "car" in fields:
        ask = (Q3_CAR_KNOWN if state.current.has_car else Q3_CAR) if "copay" in fields             else question("car", state, snapshot)
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


# about the NEW car's catalog amount only — never today's car, whose copay is the employee's own (31/10: after the
# employee typed today's amount, the general line read as if that amount still had to be checked with the rep)
COPAY_UPDATE = "אם לרכב החדש סוכם אתכם סכום אחר, אפשר לעדכן אותי ואפנה לנציג התחבורה לבירור."


def copay_rep_request_text(labels: Sequence[str], amounts: Sequence[Any]) -> str:
    """The employee asked the representative about a new car's copay: a question was opened."""
    each = ", ".join(f"{in_label(label)} ({money(a)} לפי הקטלוג)" for label, a in zip(labels, amounts))
    return (f"פניתי לנציג התחבורה לבירור ההשתתפות {each}.\n"
            "עד שיענה החישוב נשאר לפי הקטלוג, וכשיענה אעדכן אותו כאן.")


def pending_copays_text(items: Sequence[tuple[str, Any]]) -> str:
    """The new cars' copays the calculation used, each with its amount and its source (C1, 22/10): the employee never
    gave them, so the reply says they come from the catalog, are not approved yet, and were sent to the representative.
    (Called only after the calculation opened a question for each.)"""
    if len(items) == 1:
        label, amount = items[0]
        return (f"חישבתי עם השתתפות של {money(amount)} בחודש {in_label(label)}, לפי הסכום שבקטלוג שטראוס.\n"
                "הסכום עוד לא אושר עבורכם: שלחתי לנציג התחבורה בקשה לאשר אותו, וכשיענה אעדכן כאן את החישוב.")
    parts = ", ".join(f"{label} {money(amount)}" for label, amount in items)
    return (f"חישבתי לפי ההשתתפות שבקטלוג שטראוס: {parts}.\n"
            "הסכומים עוד לא אושרו עבורכם: שלחתי לנציג התחבורה בקשה לאשר כל אחד, וכשיענה אעדכן כאן את החישוב.")


def missing_copay_text(label: str) -> str:
    """A car the catalog has no copay for (C2): not calculated, never an invented amount."""
    return f"חסר לי סכום ההשתתפות {in_label(label)}, ולכן לא חישבתי אותו. שאלתי את נציג התחבורה, וכשיענה אחשב."


def map_copay_line(asked: int, total: int) -> str:
    """The option map's copay line: which cars have a question to the representative (only the ones compared, C1),
    never a pending approval that was not asked for."""
    if asked >= total:
        return "ההשתתפות בכל הרכבים לפי הקטלוג, ושלחתי לנציג התחבורה בקשה לאשר אותה."
    if asked:
        return ("במפה חישבתי כל רכב לפי ההשתתפות שבקטלוג. על הרכבים שבחרתם להשוות כבר שלחתי שאלה לנציג התחבורה;"
                " על השאר אשאל אם תבחרו בהם.")
    return ("במפה חישבתי כל רכב לפי ההשתתפות שבקטלוג. כשתבחרו רכבים להשוואה, אבקש מנציג התחבורה לאשר את"
            " ההשתתפות בהם.")


def results_reason_text(state: ConversationState, focus: Sequence[str] = ()) -> str:
    """"ולמה דווקא הראשון?" / "למה ה-NIRO פחות משתלם?" with results on screen (15/11: answered with every car's copay
    instead): the car(s) asked about — named, by their place on the list, or else the one with the highest estimated
    net — with the engine's net, change and main reason. Only figures from the results, never a new calculation."""
    ok = [i for i in state.results if i.result.get("status") == "OK" and i.result.get("delta_display") is not None
          and (i.result.get("alternative") or {}).get("net_display") is not None]
    if not ok:
        return ""
    leader = max(ok, key=lambda i: Decimal(str(i.result["delta_display"])))
    items = [i for i in ok if i.option_id in focus] or [leader]
    lines = []
    for item in items:
        r = item.result
        line = (f"{item.label}: נטו חודשי משוער {money(to_shekel(Decimal(str(r['alternative']['net_display']))))}"
                f" ({_change(Decimal(str(r['delta_display'])))} בחודש).")
        if r.get("why"):
            line += f" הסיבה העיקרית: {r['why']}"
        lines.append(line)
    if leader in items and len(ok) > 1:
        lines.append(f"זה הנטו המשוער הגבוה ביותר מבין {len(ok)} הרכבים שמוצגים.")
    return "\n".join(lines)


def results_explain_text(state: ConversationState, focus: Sequence[str] = ()) -> str:
    """"למה ההערכה זמנית?" / "זה סופי?" with results on screen: answered from the results themselves (each copay, its
    source, whether the representative was asked or answered), never by sending the employee back to choosing cars
    or by asking the representative (C5, 22/10)."""
    lines: list[str] = []
    pending = catalog = final = False
    for item in state.results:
        r = item.result
        alt = r.get("alternative") or {}
        source = r.get("copay_source")
        if source == "catalog" and alt.get("copay") is not None:
            pending = catalog = True
            lines.append(f"{in_label(item.label)} חישבתי עם {money(alt['copay'])} בחודש, סכום ברירת המחדל בקטלוג.")
        elif source == "representative" and alt.get("copay") is not None:
            effective = r.get("copay_effective_date")
            lines.append(f"{in_label(item.label)} ההשתתפות של {money(alt['copay'])} בחודש אושרה על ידי נציג התחבורה"
                         f"{_effective(date.fromisoformat(effective) if effective else None)}.")
        elif source == "catalog_final" and alt.get("copay") is not None:
            final = True
            lines.append(f"{in_label(item.label)} ההשתתפות היא {money(alt['copay'])} בחודש, לפי ברירת המחדל בקטלוג.")
        elif source == "missing":
            pending = True
            lines.append(f"{in_label(item.label)} חסר לי סכום ההשתתפות, ושאלתי את נציג התחבורה.")
    for item in state.results:
        if item.option_id in focus and item.result.get("why"):
            lines.append(f"הסיבה העיקרית {in_label(item.label)}: {item.result['why']}")
    if catalog:
        lines.insert(0, "ההערכה זמנית כי ההשתתפות ברכב החדש היא לפי ברירת המחדל בקטלוג. נציג התחבורה מאשר את "
                        "ההשתתפות שתחול עליכם בפועל, ושאלתי אותו.")
    if pending:
        lines.append("כשהנציג יענה, אעדכן כאן את החישוב. גם אז הנטו הוא חישוב לפי כללי החברה, לא תלוש שכר.")
    else:
        lines.append("החישוב לפי הנתונים שמסרתם וכללי החברה. זה חישוב משוער של הנטו, לא תלוש שכר.")
    if final:
        lines.append(COPAY_UPDATE)
    return "\n".join(lines)


def stated_copay_text(label: str, stated: Any, catalog: Any) -> str:
    """A copay the employee expects for a new car (C1): kept apart, the calculation stays on the catalog amount until
    the representative answers, and the representative gets both amounts."""
    base = f"רשמתי שאתם מצפים להשתתפות של {money(stated)} בחודש {in_label(label)}."
    if catalog is None:
        return base + "\nהחישוב ממתין לתשובת נציג התחבורה, והעברתי לו גם את הסכום שציינתם."
    return (base + f"\nבקטלוג הסכום הוא {money(catalog)}, והחישוב נשאר לפיו עד שנציג התחבורה יאשר."
            " העברתי לו גם את הסכום שציינתם.")


def group_conflict_text(stated: str, table: str, occupation: str) -> str:
    """The stated group and the occupations table differ (C4): said once, decided by the representative."""
    return (f"ציינתם קבוצה {stated}, ולפי טבלת העיסוקים {occupation} שייך לקבוצה {table}.\n"
            "את זה לא אכריע בעצמי: אשאל את נציג התחבורה איזו קבוצה חלה עליכם, אחרי שנאסוף את שאר הפרטים.")


def rep_group_text(group: str) -> str:
    return f"נציג התחבורה השיב: קבוצת הזכאות שלכם היא {group}."


def _change(delta: Decimal) -> str:
    d = to_shekel(delta)
    return f"תוספת של {money(d)}" if d > 0 else f"{money(-d)} פחות" if d < 0 else "בלי שינוי"


RESULTS_DETAIL = "הפירוט המלא בכרטיסים."


def _versus_today(delta: Any) -> str:
    """"1,163 ₪ פחות מהיום" / "300 ₪ יותר מהיום" / "כמו היום": the difference from today, in words."""
    d = to_shekel(Decimal(str(delta)))
    return f"{money(d)} יותר מהיום" if d > 0 else f"{money(-d)} פחות מהיום" if d < 0 else "כמו היום"


def highest_net_sentence(items: Sequence[Any]) -> str:
    """"MG4 (2025) משאיר את הנטו הגבוה ביותר: 12,446 ₪ בחודש, 1,163 ₪ פחות מהיום." — the option(s) with the
    highest estimated net (a tie names each), that net and its difference from today: the engine's figures, the same
    the cards show. A fact about the estimated net, never a verdict on which car is best for the employee."""
    top = max(to_shekel(Decimal(str(i.result["delta_display"]))) for i in items)
    leaders = [i for i in items if to_shekel(Decimal(str(i.result["delta_display"]))) == top]
    names = " ו-".join(i.label for i in leaders)
    verb = "משאיר" if len(leaders) == 1 else "משאירים"
    net = (leaders[0].result.get("alternative") or {}).get("net_display")
    figure = f"{money(to_shekel(Decimal(str(net))))} בחודש, " if net is not None else ""
    return f"{names} {verb} את הנטו הגבוה ביותר: {figure}{_versus_today(top)}."


def _direction(items: Sequence[Any]) -> str:
    """"בכל 3 הרכבים הנטו שלכם יקטן." / "בשני הרכבים…" / "בחלק מהרכבים הנטו שלכם יגדל ובחלק יקטן.\""""
    deltas = [to_shekel(Decimal(str(i.result["delta_display"]))) for i in items]
    where = "בשני הרכבים" if len(items) == 2 else f"בכל {len(items)} הרכבים"
    up, down, flat = any(d > 0 for d in deltas), any(d < 0 for d in deltas), any(d == 0 for d in deltas)
    if up and not down and not flat:
        return f"{where} הנטו שלכם יגדל."
    if down and not up and not flat:
        return f"{where} הנטו שלכם יקטן."
    if flat and not up and not down:
        return f"{where} הנטו שלכם לא ישתנה."
    first, second = ("יגדל", "יקטן") if up and down else ("יגדל", "לא ישתנה") if up else ("יקטן", "לא ישתנה")
    return f"בחלק מהרכבים הנטו שלכם {first} ובחלק {second}."


def reason_line(why: Optional[str], detail: str = RESULTS_DETAIL) -> str:
    """"הסיבה העיקרית: שווי השימוש גבוה יותר, ולכן המס יגדל. הפירוט המלא בכרטיסים." (the engine's main driver)."""
    return f"הסיבה העיקרית: {why} {detail}" if why else detail


# the car buttons under the alternatives question and the results (CarActions, 1/11): ONE source for their names. The
# view sends it to the screen, which draws the buttons from it, and every text that points at a button takes the name
# from here, so the two never drift apart (#10, 27/11)
CAR_ACTIONS = {"choose": "לבחור רכבים להשוואה", "change": "לשנות בחירה", "rank": "3 המשתלמים ביותר",
               "map": "כל הרכבים לפי נטו"}
RESULTS_ON_CARDS = "התוצאות מוצגות בכרטיסים, מהנטו הגבוה לנמוך."
MAP_ON_CARDS = "כל הרכבים מדורגים לפי הנטו החודשי המשוער."


def car_buttons_shown(state: ConversationState) -> bool:
    """The car buttons are on screen after this reply: the alternatives question or the results, with no question of
    code's own that has its own buttons (a value held for confirmation, an ambiguity, the offer to ask the
    representative or its draft) and no wait for the representative. The same conditions the screen draws them by."""
    if state.stage not in (Stage.CHOOSE_OPTIONS, Stage.PRESENTED):
        return False
    held = (state.pending_value_choice, state.pending_ambiguity, state.pending_none_choice, state.pending_confirmation,
            state.pending_occupation, state.pending_clarification, state.unanswered_question,
            state.pending_rep_question)
    return all(h is None for h in held)


def results_pointer() -> str:
    """Where to go from the result cards, by the buttons' own names."""
    return (f"להשוואת רכבים אחרים לחצו על \"{CAR_ACTIONS['change']}\", "
            f"ולצפייה בכל הרכבים על \"{CAR_ACTIONS['map']}\".")


def map_pointer(state: ConversationState) -> str:
    """From the option map to a comparison in the chat: the choose button as it is on screen ("לשנות בחירה" once the
    chat holds a comparison, "לבחור רכבים להשוואה" before), or the 3 with the highest net."""
    choose = CAR_ACTIONS["change"] if state.results and not results_stale(state) else CAR_ACTIONS["choose"]
    return f"לבחירת רכבים להשוואה לחצו על \"{choose}\", או על \"{CAR_ACTIONS['rank']}\"."


def results_text(state: ConversationState, buttons: Optional[bool] = None) -> str:
    """The text above the result cards. With the car buttons on screen (#10, 27/11) it repeats nothing the cards
    show: where the results are, then the buttons to go on. Without them (waiting for the representative, a value
    held for confirmation) the short summary of #9: the direction for all the cars, the car with the highest estimated
    net with that net and its difference from today, then the main reason. Either way, the lines the cards do not
    hold follow the first line: a car that was not calculated or is not eligible, a copay sent to the
    representative, a provisional today's copay."""
    buttons = car_buttons_shown(state) if buttons is None else buttons
    ok, bad = [], []
    for item in state.results:
        r = item.result
        (ok if r["status"] == "OK" and r.get("delta_display") is not None else bad).append(item)
    lines: list[str] = []
    if ok and buttons:
        lines.append(RESULTS_ON_CARDS)
    elif ok:
        flat = all(to_shekel(Decimal(str(i.result["delta_display"]))) == 0 for i in ok) and len(ok) > 1
        if state.ranked_count:
            # "הכי משתלם": how many were ranked; an option without a car by its own name ("ויתור על רכב"),
            # never as one more car
            others = state.ranked_other_labels
            cars = state.ranked_count - len(others)
            also = f", וגם {' ו'.join(others)}" if others else ""
            lines.append(f"בדקתי את כל {cars} הרכבים שאתם זכאים להם{also}. {highest_net_sentence(ok)}")
        elif len(ok) == 1:
            # one car: the settled wording ("הנטו שלכם יגדל ב-661 ₪ בחודש") and the new estimated net
            r = ok[0].result
            net = (r.get("alternative") or {}).get("net_display")
            moved = to_shekel(Decimal(str(r["delta_display"]))) != 0 and net is not None
            lines.append(f"בדקתי את {ok[0].label}. {net_change(r['delta_display'])}"
                         + (f", ל-{money(to_shekel(Decimal(str(net))))}." if moved else "."))
        elif flat:
            lines.append(_direction(ok))
        else:
            lines.append(f"{_direction(ok)} {highest_net_sentence(ok)}")
        best = max(ok, key=lambda i: Decimal(str(i.result["delta_display"])))
        lines.append(reason_line(None if flat else best.result.get("why")))
    for item in bad:
        r = item.result
        if r["status"] == "NOT_ELIGIBLE":
            lines.append(f"{item.label} לא זמין לכם: {r['reason']}")
        elif r.get("copay_source") == "missing":
            lines.append(missing_copay_text(item.label))
        else:
            lines.append(f"{item.label}: חסר מידע לחישוב. {r['reason']}")
    pending = [(i.label, (i.result.get("alternative") or {}).get("copay")) for i in ok
               if i.result.get("copay_pending") and i.result.get("copay_source", "catalog") == "catalog"]
    c = state.current
    provisional = c.copay is None and c.copay_provisional is not None   # today's copay pending with the rep (1/11)
    # the new cars' settled catalog amounts and today's amount are on the cards (#9, 27/11): no copay list here
    if provisional and ok:
        lines.append(f"החישוב כרגע מבוסס על השתתפות של {money(c.copay_provisional)} לפי הקטלוג ברכב שלכם היום, "
                     "עד לאימות מול נציג.")
    if pending and all(a is not None for _, a in pending):
        lines.append(pending_copays_text(pending))
    elif pending:
        lines.append(pending_copay_text("", None))
    if buttons:
        lines.append(results_pointer())
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
        if number != number.to_integral_value():
            # the value as typed, never rounded (31/10: "2.75" was asked back as "3 ₪")
            return f"{format(number.normalize(), ',f')} ₪"
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


def range_refused_text(field: str, value: Decimal, side: str) -> str:
    """A refused value said as a statement (another question is open): "לא רשמתי קרן השתלמות 6%, כי זה גבוה
    מהטווח שהמערכת מאפשרת. אשאל על זה שוב." The field stays missing and is asked again later."""
    return (f"לא רשמתי {_VALUE_LABELS[field][0]} {_show(field, value)}, כי זה "
            f"{'נמוך' if side == 'low' else 'גבוה'} מהטווח שהמערכת מאפשרת. אשאל על זה שוב.")


_FIELD_CTA = {
    "base_salary": "כתבו את הסכום מהתלוש, למשל: 18,500.", "commissions": "כתבו את הסכום, למשל: 1,000, או \"אין\".",
    "bonus": "כתבו את הסכום, למשל: 1,000, או \"אין\".", "pension_percent": "כתבו את האחוז, למשל: 6.",
    "study_fund_percent": "כתבו את האחוז, למשל: 2.5, או \"אין\".",
    "credit_points": "כתבו את מספר הנקודות מהתלוש, למשל: 2.25.",
    "children_under_23": "כתבו את המספר, למשל: 2, או \"אין\".",
    "current_use_value": "כתבו את הסכום מהתלוש, למשל: 4,000.", "current_copay": "כתבו את הסכום, למשל: 300.",
    "current_maintenance": "כתבו את הסכום, למשל: 1,500, או \"אין\".",
}


def field_cta(field: str) -> str:
    """The call to action for a question about one field (a re-ask after "לתקן" or a refused value)."""
    return _FIELD_CTA.get(field, "כתבו כאן את התשובה.")


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


def held_text(pending: Any) -> str:
    """The question for a value held for confirmation; several fields held together are asked once (1/11)."""
    if pending.also:
        parts = [f"{_VALUE_LABELS[f][0]} {_show(f, v)}" for f, v in [(pending.field, pending.value), *pending.also.items()]]
        return f"כדי לוודא שהבנתי: לרשום {' ו'.join(parts)}?"
    return confirm_value_text(pending.field, pending.value, pending.unusual, pending.scale)


def held_fix_text(pending: Any) -> str:
    """"לתקן" on values held together: each field is asked again, in one question (1/11)."""
    if pending.also:
        names = [_VALUE_LABELS[f][0] for f in (pending.field, *pending.also)]
        return f"בסדר. כמה אחוז יורד לכם ל{' ול'.join(names)}? אם אחד מהם 0, כתבו 0."
    return unusual_fix_text(pending.field)


def confirm_value_text(field: str, value: Decimal, unusual: bool = False, scale: bool = False) -> str:
    if scale:
        return f"התכוונתם ל-{_show(field, value)} או ל-{_show(field, value * 1000)}?"
    if unusual:
        ask, note = _UNUSUAL_ASK.get(field, (f"לאשר {_VALUE_LABELS[field][0]} {{v}}?", "הערך נדיר."))
        return f"{ask.format(v=_show(field, value))} {note}"
    return f"התכוונתם ל-{_show(field, value)}?"


def in_label(label: str) -> str:
    """"ב-ATTO 3 (2025)" before a Latin name; "בוויתור על רכב" before a Hebrew one (no hyphen, ו doubled)."""
    if label[:1] and "א" <= label[0] <= "ת":
        return f"בו{label}" if label.startswith("ו") and not label.startswith("וו") else f"ב{label}"
    return f"ב-{label}"


def approximate_assumptions(state: ConversationState) -> list[str]:
    """"עמלות: כ-1,000 ₪, לפי ההערכה שלכם" — לכל ערך שהעובד אמר כהערכה."""
    p, c = state.profile, state.current
    values = {**profile_args(p), "current_use_value": c.use_value, "current_copay": c.copay, "current_maintenance": c.maintenance}
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


def _effective(effective: Optional[date], today: Optional[date] = None) -> str:
    """When the representative's amount applies: "יחול מ-" for a future date (never shown as in force today, D7),
    "בתוקף מ-" once it is."""
    if not effective:
        return ""
    if effective > (today or date.today()):
        return f" (יחול מ-{effective:%d.%m.%Y})"
    return f" (בתוקף מ-{effective:%d.%m.%Y})"


def _effective_words(effective: Optional[date], today: Optional[date] = None) -> str:
    """The same, as a sentence for a chat line (no second parenthesis after a car label)."""
    if not effective:
        return ""
    verb = "תחול" if effective > (today or date.today()) else "בתוקף"
    return f" ההשתתפות {verb} מ-{effective:%d.%m.%Y}."


def confirmed_assumption(label: str, effective: Optional[date]) -> str:
    return f"השתתפות ב-{label} — אושר על ידי נציג התחבורה{_effective(effective)}"


def rep_amount_assumption(label: str, amount: Decimal, effective: Optional[date]) -> str:
    """הסכום שהנציג קבע (שונה מהקטלוג) — מופיע בהנחות של כל חישוב של החלופה."""
    return f"השתתפות ב-{label}: {money(amount)} לפי נציג התחבורה{_effective(effective)}"


def rep_confirmed_text(label: str, effective: Optional[date], amount: Any = None, delta: Any = None) -> str:
    """The representative approved the catalog amount: the result does not change, and it is now final."""
    what = f": {money(amount)} בחודש" if amount is not None else ""
    head = f"נציג התחבורה אישר את ההשתתפות {in_label(label)}{what}.{_effective_words(effective)}"
    if delta is None:
        return head
    return f"{head}\nהחישוב כבר היה לפי הסכום הזה, ולכן לא השתנה: {net_change(delta)}."


def _delta_words(delta: Any) -> str:
    v = to_shekel(Decimal(str(delta)))
    return f"תוספת של {money(v)}" if v > 0 else f"{money(-v)} פחות" if v < 0 else "בלי שינוי"


def rep_changed_text(label: str, amount: Decimal, effective: Optional[date], old_delta: Any, new_delta: Any,
                     catalog: Any = None, today_net: Any = None, new_net: Any = None, why: str = "") -> str:
    """The demo's key moment (the challenge script, p. 2): the amount the representative set, from when, the net
    today and with the car after the recalculation, the change before and after, and the main reason. Numbers come
    only from the representative's answer, the catalog, the previous result and the new result."""
    was = f", במקום {money(catalog)} לפי הקטלוג" if catalog is not None else ""
    lines = [f"נציג התחבורה קבע השתתפות של {money(amount)} בחודש {in_label(label)}{was}.{_effective_words(effective)}"]
    if today_net is not None and new_net is not None:
        lines.append(f"חישבתי מחדש: עם {label} הנטו יהיה {money(new_net)} בחודש, לעומת {money(today_net)} היום.")
    before = f" לפני האישור: {_delta_words(old_delta)}." if old_delta is not None else ""
    lines.append(f"{net_change(new_delta)}.{before}")
    if why:
        lines.append(f"הסיבה העיקרית: {why}")
    return "\n".join(lines)


def rep_copay_text(label: str, amount: Decimal, effective: Optional[date]) -> str:
    """The representative's copay when the result on record is out of date: the amount only (the net
    comes from the recalculation, never from the old result)."""
    return f"נציג התחבורה קבע השתתפות של {money(amount)} בחודש {in_label(label)}.{_effective_words(effective)}"


def rep_current_text(field: str, label: str, amount: Decimal) -> str:
    what = "ההשתתפות הנוכחית שלכם" if field == "CURRENT_COPAY" else "שווי השימוש"
    return f"נציג התחבורה השיב: {what} ב-{label} היא {money(amount)}."


def rep_copay_offer_text(label: str, amount: Decimal, previous: Optional[Decimal],
                         provisional: Optional[Decimal] = None) -> str:
    """The representative's amount for today's copay, offered (1/11): applied only on the employee's confirmation."""
    head = f"נציג התחבורה השיב: ההשתתפות החודשית שלכם ב-{label} היא {money(amount)}."
    if previous is None and provisional is not None:
        return (f"{head} החישוב הנוכחי מבוסס על {money(provisional)} לפי הקטלוג. "
                "אפשר לעדכן את הסכום ולחשב מחדש לפי תשובת הנציג.")
    if previous is not None and Decimal(str(previous)) != amount:
        return (f"{head} כרגע החישוב לפי {money(previous)} שמסרתם. "
                "אפשר לעדכן את החישוב לפי תשובת הנציג; העדכון יחליף את הסכום הקודם.")
    return f"{head} אפשר לעדכן את החישוב לפי תשובת הנציג."


def rep_copay_applied_text(amount: Decimal, previous: Optional[Decimal]) -> str:
    """After the employee confirmed the representative's amount (1/11)."""
    if previous is not None and Decimal(str(previous)) != amount:
        return f"עדכנתי לפי תשובת הנציג: השתתפות {money(amount)} במקום {money(previous)}."
    return f"עדכנתי לפי תשובת הנציג: השתתפות {money(amount)}."


def rep_opt_out_text(allowed: bool) -> str:
    if allowed:
        return ("נציג התחבורה השיב שבעיסוק שלכם מותר לוותר על הרכב. "
                "שאלתי אותו מה סכום אחזקת הרכב החודשי; אעדכן כשתגיע תשובה.")
    return f"{OPT_OUT_DENIED}."


_MARKUP_RE = re.compile(r"\*\*|__|`|<[^>]*>|[<>]")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
NOTE_MAX = 500


def rep_note_text(note: Optional[str]) -> str:
    """The representative's note to the employee, as written: its line breaks and punctuation are kept; control
    characters, bold, links and HTML are removed, empty lines collapse to one, at most NOTE_MAX characters. No note
    → "" (no placeholder line)."""
    if not note:
        return ""
    text = note.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch if ch == "\n" else " " if unicodedata.category(ch) in ("Cc", "Cf", "Zl", "Zp") else ch
                   for ch in text)
    text = _MARKUP_RE.sub("", _LINK_RE.sub(lambda m: m.group(1), text))
    lines = [" ".join(line.split()) for line in text.split("\n")]
    clean = re.sub(r"\n{2,}", "\n", "\n".join(lines))[:NOTE_MAX].strip()
    return f"הערת הנציג: {clean}" if clean else ""


def form_saved_text(previous: dict[str, Any], saved: dict[str, Any], source: Optional[str] = None) -> str:
    """What the form / a reviewed payslip changed: "עדכנתי: שכר בסיס 18,000 ₪ במקום 15,000 ₪." for a value
    that replaced another, "רשמתי: …" for a new one. A value equal to the one already saved is not repeated."""
    def same(a: Any, b: Any) -> bool:
        return a is not None and b is not None and Decimal(str(a)) == Decimal(str(b))

    changed = [f"{recorded_text({k: v})} במקום {_show(k, previous[k])}" for k, v in saved.items()
               if previous.get(k) is not None and not same(previous[k], v)]
    new = {k: v for k, v in saved.items() if previous.get(k) is None}
    where = " מהתלוש" if source == "payslip" else ""
    lines = []
    if changed:
        lines.append(f"עדכנתי{where}: {', '.join(changed)}.")
    if new:
        lines.append(f"רשמתי{where}: {recorded_text(new)}.")
    if not lines and saved:
        lines.append("הערכים זהים למה שכבר רשמתי.")
    return "\n".join(lines)


def rep_today_maintenance_text(amount: Decimal, kept: Optional[Decimal]) -> str:
    """The representative's answer about the employee's own car-maintenance allowance (no car today, 19/11). kept: a
    value the employee gave meanwhile, which is not replaced by itself (it can be changed in "הנתונים שלי")."""
    if kept is not None:
        return (f"נציג התחבורה השיב: אחזקת הרכב שלכם היא {money(amount)} בחודש. בינתיים מסרתם {money(kept)}, "
                "והחישוב נשאר לפיו. אם צריך לעדכן, אפשר לעשות זאת ב\"הנתונים שלי\".")
    return f"נציג התחבורה השיב: אחזקת הרכב שלכם היא {money(amount)} בחודש. עדכנתי את הנתון."


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
        RepReason.ELIGIBILITY_GROUP: "איזו קבוצת זכאות חלה עליכם",
    }
    if state.pending_rep.reason == RepReason.NEW_CAR_COPAY:
        return WAITING_COPAY
    return f"שאלתי את נציג התחבורה {topics[state.pending_rep.reason]}. אעדכן כשתגיע תשובה."


def confirm_group_text(occupation: str, group: str) -> str:
    """שורת אישור לקבוצה שנקבעה לפי התפקיד — מהפרופיל שהכלי שמר."""
    return f"זיהיתי: {occupation}, קבוצה {group}."


def chosen_options_text(labels: list[str]) -> str:
    return f"בחרתם להשוות: {', '.join(labels)}."


# cars chosen while a value the calculation needs is still missing (16/11: the choice got no cards and no word why)
COMPARE_WAITS = "את ההשוואה אחשב ברגע שיהיו לי הפרטים שחסרים עדיין."


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
    # the car's catalog group is not the employee's eligibility (31/10: group א with a COROLLA of group ב read as a
    # contradiction): said once, so it is clear which group the alternatives come from
    own = state.profile.group
    whose = (f" זו הקבוצה של הרכב בקטלוג; את החלופות אבדוק לפי קבוצת הזכאות שלכם, {own}."
             if c.group and own and c.group != own and c.group != FAMILY_GROUP else "")
    replaced = state.previous_values.get("current_car")
    if replaced and replaced != c.label:
        # another car than the one on record: the old and the new one, once (15/11), and a copay that stayed from it
        carried = (f" ההשתתפות {money(c.copay)} נשארה מהרכב הקודם; אם היא שונה ברכב הזה, עדכנו אותה."
                   if c.copay_source == "carried" and c.copay is not None else "")
        return f"עדכנתי: הרכב של היום {', '.join(details)} במקום {replaced}.{whose}{carried}"
    return f"זיהיתי: {', '.join(details)}.{whose} אם זה לא הרכב שלכם, אפשר לתקן."




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
    saved_first: bool = False                            # the answers are computed from what this turn saved
    # ---- the turn decision (reply contract, 16/11): what the turn understood, changed and owes, decided in code ----
    intent: list[str] = field(default_factory=list)      # what the message was read as (values, explain:<id>, …)
    changes: dict[str, str] = field(default_factory=dict)   # the values saved this turn (state diff)
    missing: list[str] = field(default_factory=list)     # what a calculation still needs (fields)
    results: bool = False                                # this turn calculated: the cards show its results
    explainable: list[str] = field(default_factory=list)  # the verified answers the model's explanation may replace
    answer_source: Optional[str] = None                  # "model" / "template" (an explanation was owed), else None

    def with_explanation(self, text: str) -> "ReplyPlan":
        """The same decision, with the model's verified explanation in place of the template's own explanation
        (the FAQ answers / the results' reason it was asked for). Every other part stays code's."""
        if not self.explainable:
            return self
        at = next((i for i, a in enumerate(self.answers) if a in self.explainable), len(self.answers))
        kept = [a for a in self.answers if a not in self.explainable]
        return replace(self, answers=[*kept[:at], text, *kept[at:]], answer_source="model")

    def decision(self) -> dict[str, Any]:
        """The turn decision, for the record and the tests (no salary values in logs)."""
        return {"intent": list(self.intent), "changes": dict(self.changes), "acks": list(self.ack),
                "answer_source": self.answer_source, "missing_fields": list(self.missing), "results": self.results,
                "ask": self.ask[-1] if self.ask else "", "cta": self.cta, "fixed": self.fixed}

    def text(self) -> str:
        # a question is answered first; but when the answer is computed from what this turn saved (saved_first: "בעצם
        # אני בקבוצה ג. ועכשיו כמה?", 16/11), the change is said first, once, and the answer that reflects it follows
        if self.saved_first:
            saved = [a for a in self.ack if a.startswith(SAVED_STARTS)]
            rest = [a for a in self.ack if not a.startswith(SAVED_STARTS)]
            parts = [x for x in [*saved, *self.answers, *rest, *self.follow_up, *self.ask, self.cta] if x]
        else:
            parts = [x for x in [*self.answers, *self.ack, *self.follow_up, *self.ask, self.cta] if x]
        # safety net only (the root cause is handled where the parts are built): never emit the same line
        # twice in one reply. Dedup at the line level across the whole reply, but keep each block's own
        # multi-line structure (so a list or a two-line question is preserved). First occurrence wins.
        seen: set[str] = set()
        kept_blocks: list[str] = []
        for part in parts:
            lines = []
            for line in part.splitlines():
                key = line.strip()
                if not key or key not in seen:
                    if key:
                        seen.add(key)
                    lines.append(line)
            block = "\n".join(lines).strip()
            if block:
                kept_blocks.append(block)
        return "\n\n".join(kept_blocks)

    def as_json(self) -> dict[str, Any]:
        return {"answers": self.answers, "ack": self.ack, "follow_up": self.follow_up, "ask": self.ask,
                "cta": self.cta}


CTA_STARTS = ("כתבו", "בחרו", "אפשר לכתוב")
# a line that says what this turn saved or identified: it opens the reply
SAVED_STARTS = ("רשמתי", "עדכנתי", "זיהיתי")
NEXT_AFTER_RESULTS = "רוצים לראות אילו רכבים משאירים לכם את הנטו הגבוה ביותר?"
NEXT_AFTER_RANK = "רוצים להשוות רכבים אחרים? כתבו את השמות שלהם."
# 24/11: the one follow-up line after a small-talk or off-topic message, by where the conversation is
ASIDE_REP_DRAFT = 'הפנייה לנציג התחבורה מחכה לאישור שלכם: בחרו "לשלוח" או "לבטל".'
ASIDE_REP_OFFER = 'רוצים שאשאל את נציג התחבורה? בחרו "לשאול את הנציג" או "להמשיך בלי".'
ASIDE_REP_WAITING = "שאלתי את נציג התחבורה, והתשובה תגיע לכאן בשיחה."
ASIDE_REP_OPEN = "השאלה שלכם לנציג התחבורה עדיין פתוחה, והתשובה תגיע לכאן."
ASIDE_AFTER_RANK = "אפשר להשוות רכבים אחרים, או לשאול איך חישבתי את הנטו."
LINK_LABELS = {"profile_form": "למלא בטופס", "selection": CAR_ACTIONS["choose"],
               "payslip_upload": "להעלות תלוש שכר", "correction_form": "לפתוח את טופס תיקון הנתונים"}

# a correction of the employee's own data (1/11): code asks what, or the new value; saved like the form, then every
# result and the option map are recalculated. Never a question for the representative.
CORRECTION_ASK = ("אפשר לתקן כל נתון שמסרתם, והחישוב יתעדכן.\n"
                  "מה תרצו לתקן? כתבו את הנתון והערך הנכון, למשל: \"השכר שלי 20,000\" או \"פנסיה 6%\".")
CORRECTION_CARS = f"כדי לשנות את הרכבים בהשוואה, לחצו על \"{CAR_ACTIONS['change']}\"."
CORRECTION_CTA = "אפשר גם לפתוח את טופס תיקון הנתונים."
# a bare "אין" to a question about several salary values (23/11): what it is about is asked, never spread as zeros.
# id -> (button label, the fields it records as 0); a button is offered only when all its fields are asked
NONE_CHOICES = {
    "none_commissions_bonus": ("אין עמלות ואין בונוס", ("commissions", "bonus")),
    "none_children": ("אין ילדים עד גיל 23", ("children",)),
    "none_all": ("אין עמלות, בונוס, קרן השתלמות וילדים", ("commissions", "bonus", "study_fund_percent", "children")),
}
NONE_QUESTION = 'על מה ה"אין"?'
NONE_CTA = 'בחרו אחת מהאפשרויות, או כתבו במפורש, למשל: "אין עמלות ואין בונוס".'
CORRECTION_VALUE_CTA = "כתבו רק את הערך הנכון, והחישוב יתעדכן."


def correction_field_ask(state: ConversationState, arg: str) -> str:
    """The new value of one field, with what is recorded now (the employee's own data, said back to them)."""
    if arg == "group":
        now = state.profile.group
        return "מה קבוצת הזכאות הנכונה (א–ד)?" + (f" רשום אצלי עכשיו: קבוצה {now}." if now else "")
    name = _VALUE_LABELS[arg][0]
    value = (state.current.copay if arg == "current_copay"
             else getattr(state.profile, PROFILE_ARGS[arg], None))
    now = f" רשום אצלי עכשיו: {_show(arg, value)}." if value is not None else ""
    return f"מה הערך הנכון של {name}?{now}"


def _names(fields: Sequence[str]) -> str:
    names = [_UNKNOWN_NAMES[f] for f in fields if f in _UNKNOWN_NAMES]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " ו" + names[-1]


def unknown_text(state: ConversationState, snapshot: RulesSnapshot, fields: Sequence[str]) -> str:
    """Only fields the employee does not know are left: no calculation, never a default. Said once with
    where to find each value; asked again ("לא יודע") in other words, never the same text twice in a row."""
    waiting = [f for f in fields if with_rep(state, f)]
    open_ = [f for f in fields if f not in waiting]
    estimable = any(f in ESTIMABLE for f in fields)
    estimate = " אם אתם יודעים בערך, כתבו את הערך ואסמן את התוצאה כהערכה." if estimable else ""
    if waiting:
        # sent to the representative (19/11): the answer is on its way — never "maybe you found it", never offered again
        waited = _names(waiting)
        lines = [f"החישוב מחכה ל{waited[1:] if waited.startswith('ה') else waited}: "
                 "שאלתי את נציג התחבורה, והתשובה תגיע לכאן ואחשב מיד. לא אנחש ולא אציב 0 בינתיים."
                 + (" אם אתם יודעים בערך, אפשר לכתוב הערכה ואסמן את התוצאה כמשוערת." if estimable else "")]
        if open_:
            lines.append(f"חסר עוד: {_names(open_)}.")
        return "\n".join(lines)
    if fields == ["maintenance"]:
        # no car today, the allowance alone (19/11): what is missing, where it is (only if there is one), and every way on
        entry = snapshot.faq.get("maintenance")
        where = f" {entry.where}" if entry is not None and entry.where else ""
        if state.unknown_told % 2:
            return ("בסדר. החישוב מחכה רק לסכום אחזקת הרכב: בלעדיו אין נטו אמין, ולכן לא אנחש ולא אציב 0. "
                    "אפשר לברר מול נציג, לכתוב הערכה ואסמן אותה, או לכתוב \"אין\" אם אינכם מקבלים אחזקה.")
        return (f"כדי לחשב את הנטו שלכם היום, חסר לי עוד סכום אחזקת הרכב.{where} "
                "אם אינכם מקבלים אחזקה, כתבו \"אין\". אפשר גם לברר מול נציג, או לכתוב הערכה ואסמן אותה בתוצאות.")
    names = _names(fields)
    if state.unknown_told % 2:
        # still unknown (16/11): no reliable net yet, said briefly; everything else goes on, and a value the employee
        # knows roughly is welcome, marked as an estimate. Never a value of the system's own, never 0.
        to = names[1:] if names.startswith("ה") else names        # ל + ההשתתפות = להשתתפות
        return (f"בסדר. החישוב מחכה רק ל{to}: בלעדיו אין נטו אמין, ולכן לא אנחש ולא אציב 0.{estimate} "
                "בינתיים אפשר לשאול על הרכבים, הזכאות וההשתתפות בכל רכב.")
    these = "הערך הזה" if len(fields) == 1 else "הערכים האלה"
    # the calculation needs them now: asked again gently, once, with where to find them (16/11)
    lines = [f"כדי לחשב את הנטו אני צריך עכשיו את {names}. אולי כבר מצאתם {these}?"
             + (" אם אתם יודעים רק בערך, אפשר לכתוב הערכה ואסמן אותה בתוצאות." if estimable else "")]
    where = [f for f in fields if (e := snapshot.faq.get(f)) is not None and e.where]
    if len(where) <= 2:           # where on the payslip, field by field — short
        lines += [f"{_UNKNOWN_NAMES[f]}: {snapshot.faq.get(f).where}" for f in where]
    else:
        lines.append("כולם מופיעים בתלוש השכר החודשי.")
    return "\n".join(lines)
UNANSWERED = "על זה אין לי מידע מדויק. התשובה של נציג התחבורה תגיע לכאן. רוצים שאשאל אותו?"
# a car property the catalog does not hold (colour, equipment, sunroof, stock: faq.attribute_terms, 19/11): said as
# unverified, never "not possible" and never a stock claim; the representative can check it
ATTRIBUTE_UNVERIFIED = ("צבע, אבזור וזמינות של רכב לא מופיעים בקטלוג שלי, כך שאין לי מידע מאומת עליהם ולא אקבע אם זה "
                        "אפשרי. נציג התחבורה יכול לבדוק את זה עבורכם. רוצים שאשאל אותו?")


def unanswered_offer(state: ConversationState, snapshot: RulesSnapshot) -> str:
    """The offer to ask the representative, in words that fit what is open: a car property the catalog cannot
    verify is said as such (19/11); any other question keeps the general line."""
    from .understanding import asks_attribute      # lazy: understanding imports this module's neighbours
    question = state.unanswered_question or ""
    return ATTRIBUTE_UNVERIFIED if question and asks_attribute(snapshot, question) else UNANSWERED
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

    p = state.profile
    fields = [*open_profile(p), *missing_current_car(state.current), *unknown_missing(p)]
    if not state.selected and not state.rank_requested and not state.map_requested:
        fields.append("options")       # the option map needs no choice of cars
    # today's copay or use value the employee does not know (rule A): asked from the employee, last
    return [*(FIELD_NAMES[f] for f in fields if f in FIELD_NAMES),
            *(_UNKNOWN_NAMES[f] for f in current_unknown(state.current))]


def compare_needs_text(state: ConversationState) -> str:
    """A request the bot cannot do yet ("תשווה ביניהם" before the profile is complete): what is needed."""
    names = missing_names(state)
    if not names:
        return ""
    if not state.map_requested and any(f in ("occupation", "group", "base") for f in open_profile(state.profile)):
        # advice asked before the basics are known ("כדאי לי להחליף רכב?"): what the answer depends on, never the
        # whole list of missing fields; one question follows (30/10)
        return ADVICE_FIRST
    if names == [FIELD_NAMES["options"]]:
        # "כדאי לי להחליף את הרכב?" with everything known but the cars: what the answer depends on, never the
        # missing choice twice (live QA 31/10: "חסרים לי עוד: הרכבים להשוואה" + "עדיין חסר לי: הרכבים להשוואה")
        return CHOOSE_TO_COMPARE
    what = "למפות את כל האפשרויות" if state.map_requested else "להשוות"
    return f"כדי {what}, חסרים לי עוד: {_list(names)}."


# the question about the language engine answered without the model (an outage, a cap, none configured): said (31/10)
# "אני רוצה להעלות תלוש" (1/11): the uploader opens at once; what happens to the file, said before it is chosen
PAYSLIP_OFFER = ("בשמחה, אפשר להתחיל מהתלוש. לחצו על \"להעלות תלוש שכר\" ובחרו קובץ PDF או תמונה. "
                 "אקרא ממנו את פרטי השכר ואציג לכם אותם לבדיקה ולתיקון, ורק אחרי שתאשרו אשתמש בהם בחישוב. "
                 "התלוש עצמו לא נשמר.")
# a new payslip over values already given (19/11): nothing is replaced before the employee sees and approves it
PAYSLIP_REPLACE_OFFER = ("אפשר להעלות תלוש שכר חדש. לחצו על \"להעלות תלוש שכר\" ובחרו קובץ PDF או תמונה. "
                         "אציג לכם כל שינוי מול הנתונים שכבר רשמתי, ורק אחרי שתאשרו אעדכן אותם ואחשב מחדש. "
                         "התלוש עצמו לא נשמר.")
PAYSLIP_UNAVAILABLE = ("העלאת תלוש לא זמינה כרגע. אפשר לכתוב כאן את פרטי השכר מהתלוש, "
                       "למשל: \"שכר בסיס 18,000, פנסיה 6%\".")


# today's copay sent to the representative (31/10): no amount is assumed until the answer
COPAY_WITH_REP = ("רשמתי: את ההשתתפות ברכב שלכם היום נברר מול נציג התחבורה, והתשובה תגיע לכאן. "
                  "עד אז לא אחשב נטו ולא אציב סכום משלי. אם אתם יודעים בערך, אפשר לכתוב הערכה ואסמן את "
                  "התוצאות כמשוערות.")


def copay_with_rep_text(provisional: Optional[Decimal]) -> str:
    """Today's copay sent to the representative (1/11): with a catalog amount the calculation goes on with it,
    provisionally and said so; without one, nothing is assumed (COPAY_WITH_REP)."""
    if provisional is None:
        return COPAY_WITH_REP
    return ("רשמתי: את ההשתתפות ברכב שלכם היום נברר מול נציג התחבורה, והתשובה תגיע לכאן. "
            f"החישוב כרגע מבוסס על השתתפות של {money(provisional)} לפי הקטלוג, עד לאימות מול נציג.")
ENGINE_OFFLINE = "התשובה הזו לא נכתבה על ידי Gemini: כרגע מנוע השפה לא זמין, והיא מגיעה מתבנית קבועה."

ADVICE_FIRST = ("זה תלוי ברכב שלכם היום, ברכבים שאתם זכאים להם ובנטו שיישאר לכם אחרי ההחלפה. "
                "כדי לבדוק, אצטרך כמה פרטים, ונתחיל באחד.")
CHOOSE_TO_COMPARE = ("זה תלוי בנטו שיישאר לכם בכל רכב לעומת המצב שלכם היום. "
                     "אחשב את זה לכל רכב שתבחרו, או לכל הרכבים שאתם זכאים להם.")


ALL_AT_ONCE = "אני שואל בשלבים כדי שלא יחסר דבר, אבל אפשר לענות על הכול בהודעה אחת."

_PROFILE_ARGS = tuple(PROFILE_ARGS.items())


def progress_text(state: ConversationState, fields: Sequence[str]) -> str:
    """The same compound question again: what is already saved, then only what is still missing
    ("רשמתי: שכר בסיס 18,500 ₪, פנסיה 5%. עדיין חסרים לי: עמלות, בונוס…"), never the full question verbatim."""
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
_GROUP_SIMPLE = "במילים פשוטות: לאיזו קבוצת רכב אתם זכאים, א, ב, ג או ד? אם אתם לא יודעים, כתבו את התפקיד שלכם."


def _faq_example(snapshot: RulesSnapshot, key: str) -> str:
    entry = snapshot.faq.get(key)
    return entry.example if entry is not None and entry.example else EXAMPLES.get(key, "").split(", ")[0]


def _is_q2(fields: Sequence[str]) -> bool:
    return bool(fields) and all(f in Q2_FIELDS for f in fields)


def call_to_action(state: ConversationState, snapshot: RulesSnapshot,
                   fields: Optional[Sequence[str]] = None) -> tuple[str, Optional[str]]:
    """The one call to action that ends a reply, and the one link it names (or None)."""
    if state.link_now == "payslip_upload":
        # the employee asked to upload a payslip (1/11): the uploader, whatever the flow would ask next. Typing stays
        # open (19/11): the question on screen can still be answered in words, never only "upload"
        asked = current_group(state)[1]
        if asked and asked[0] in PROFILE_ARGS.values():
            example = _faq_example(snapshot, asked[0]).split(", ")[0]
            return f"לחצו על \"להעלות תלוש שכר\" ובחרו את הקובץ, או כתבו כאן, למשל: \"{example}\".", "payslip_upload"
        return "לחצו על \"להעלות תלוש שכר\" ובחרו את הקובץ.", "payslip_upload"
    if state.link_now == "correction_form":
        # a correction asked for (1/11): the form is the other way to give it
        return (CORRECTION_VALUE_CTA if state.correcting_field else CORRECTION_CTA), "correction_form"
    if state.pending_rep_question is not None:
        return "בחרו \"לשלוח\" או \"לבטל\".", None
    if state.unanswered_question is not None:
        return REP_OFFER_CTA, None
    if state.pending_value_choice is not None:
        return "בחרו אחת מהאפשרויות, או כתבו במילים.", None
    if state.pending_ambiguity is not None:
        return "בחרו אחת מהאפשרויות, או כתבו \"אין רכב\" או את דגם הרכב.", None
    if state.pending_none_choice:
        return NONE_CTA, None
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
        if all(with_rep(state, f) for f in fields):
            # the representative was asked (19/11): nothing to look for; an estimate is still welcome
            return ("אפשר להמשיך לשאול בינתיים, או לכתוב הערכה, למשל: \"בערך 1,000\"."
                    if any(f in ESTIMABLE for f in fields) else "אפשר להמשיך לשאול בינתיים."), None
        if fields == ["maintenance"]:
            return "בחרו אחת מהאפשרויות, או כתבו את הסכום, למשל: 1,500, או \"אין\".", None
        # the payslip is offered only when a value asked is printed on it (19/11: not for the number of children alone),
        # and not again once a payslip was reviewed. 23/11: the upload itself only when payslip_allowed — the bot's one
        # offer, or asked for by the employee (the rule of the salary questions)
        on_payslip = any(f not in _NOT_ON_PAYSLIP for f in fields) and not state.payslip_reviewed
        link = "payslip_upload" if on_payslip and payslip_allowed(state, snapshot) else None
        example = _faq_example(snapshot, fields[0]).split(", ")[0]
        if not on_payslip:
            return f"כתבו את הערך, למשל: \"{example}\".", None
        return (f"כתבו את הערך כשתמצאו אותו בתלוש, למשל: \"{example}\"" + (", או העלו את התלוש." if link else "."),
                link)
    # the field the question text asks (group_question's order), not just the first open one
    first = next((f for f in ("car", "copay", "opt_out_occupation", "options") if f in fields), fields[0])
    if first == "copay" and catalog_copay_hint(state, snapshot) is not None:
        return "בחרו אחת משלוש האפשרויות.", None          # the three copay choices (31/10)
    if first == "copay" and copay_choices_offered(state, snapshot):
        return "בחרו אחת מהאפשרויות, או כתבו את הסכום.", None   # no catalog amount: a known amount or the rep (1/11)
    if _is_q2(fields):
        example = q2_example(fields) if len(fields) > 1 else _faq_example(snapshot, first)
        # "all at once" (state.link_now set by the turn) wants the form, to fill everything together.
        # Otherwise, when a payslip upload is allowed now, offer it (once) instead of the manual form link,
        # so the employee can upload rather than type. Only one Q2 link ever. 22/11: "allowed" is the one rule of
        # actions.payslip_allowed, so once a payslip was confirmed it is not offered again unless asked for.
        if state.link_now != "profile_form" and payslip_allowed(state, snapshot):
            link = _once(state, "payslip_upload")
            return f"אפשר לכתוב כאן, למשל: \"{example}\"" + (", או להעלות תלוש." if link else "."), link
        if ((state.payslip_reviewed or state.payslip_offered) and not state.payslip_requested
                and state.link_now != "profile_form"):
            # 22/11: after a confirmed payslip the salary questions are asked in text only: no upload, no form.
            # 23/11: the same once the bot offered the upload by itself (one offer; asked for, it comes back)
            return f"אפשר לכתוב כאן, למשל: \"{example}\".", None
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
        # a company car the employee already said they have: only the model is asked, never "אם אין רכב"
        no_car = "" if state.current.has_car else " אם אין רכב, כתבו \"אין רכב\"."
        if "copay" in fields:
            return f"כתבו את הדגם וכמה משלמים, למשל: \"{_faq_example(snapshot, 'car')}\".{no_car}", None
        return f"כתבו את הדגם, למשל: \"קורולה קרוס 2025\".{no_car}", None
    if first == "copay":
        return "כתבו את הסכום, למשל: 300, או \"לא ידוע\".", None
    if first == "use_value":
        return "כתבו את הסכום מהתלוש, למשל: 4,000, או \"לא ידוע\".", None
    if first == "drive":
        return "בחרו או כתבו, למשל: היברידי.", None
    if first == "maintenance":
        # the closed choices (19/11): with the payslip's amount, three; without one, an amount, "אין" or the rep
        if state.payslip_maintenance is not None:
            return "בחרו אחת משלוש האפשרויות.", None
        return "בחרו אחת מהאפשרויות, או כתבו את הסכום, למשל: 1,500, או \"אין\", או \"לא ידוע\".", None
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
    if state.pending_value_choice is not None:
        return "למשל: לשניהם"
    if state.pending_ambiguity is not None:
        return "למשל: אין רכב"
    if state.pending_none_choice:
        return "למשל: אין עמלות ואין בונוס"
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
    fields = paced(state, fields)          # "one question at a time": the explanation names that one field too
    if not fields:
        return ""
    group = current_group(state)[0]
    first = next((f for f in ("car", "copay", "opt_out_occupation", "options") if f in fields), fields[0])
    if _is_q2(fields) and len(fields) > 1:
        return _Q2_SIMPLE
    if fields in (["occupation"], ["group"]):
        return _GROUP_SIMPLE
    key = ("occupation" if group == "Q1" and len(fields) > 1
           else "car" if first in ("car", "copay") and "car" in fields else first)
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


_DRIVE_PLURAL = {"PETROL": "רכבי בנזין", "HYBRID": "היברידיים", "PLUG_IN": "פלאג-אין", "ELECTRIC": "חשמליים"}


def eligible_text(state: ConversationState, snapshot: RulesSnapshot, drive: str = "", note: bool = False) -> str:
    """The cars open to the employee, from the eligibility rules after this turn's saves (15/11): the count and the
    names (the same list the alternatives question offers), or those of one drive. Never a guess: no group, no list.
    note: the family-track line, only when it may change the answer (children unknown) and said once (16/11)."""
    group = state.profile.group
    if not group:
        return "כדי לדעת אילו רכבים פתוחים לכם, צריך את קבוצת הזכאות שלכם."
    cars = [o for o in available_options(snapshot, state) if o.car is not None]
    today = " (בלי הרכב שלכם היום)" if state.current.catalog_code else ""
    names = lambda options: ", ".join(o.label for o in options)       # noqa: E731
    # one drive, or several ("HYBRID,PLUG_IN": "חוסך דלק אבל עדיין עם מנוע", 15/11): the reply names each drive it
    # filtered by, so the employee sees how the question was read
    drives = [d for d in drive.split(",") if d in DRIVE_HE] if drive else []
    if not drives:
        lines = [f"בקבוצה {group} ומטה פתוחים לכם {len(cars)} רכבים{today}:", f"{names(cars)}."]
    else:
        picked = [o for o in cars if o.drive is not None and o.drive.value in drives]
        single = " או ".join(DRIVE_HE[d] for d in drives)
        plural = " או ".join(_DRIVE_PLURAL[d] for d in drives)
        head = f"מתוך {len(cars)} הרכבים שפתוחים לכם בקבוצה {group} ומטה"
        if not picked:
            lines = [f"{head}, אין רכב {single}."]
        elif len(picked) == 1:
            kind = DRIVE_HE[picked[0].drive.value] if len(drives) > 1 else single
            lines = [f"{head}, רכב {kind} אחד: {picked[0].label}."]
        else:
            lines = [f"{head}, {len(picked)} {plural}:", f"{names(picked)}."]
    family = [c for c in snapshot.catalog if c.group == FAMILY_GROUP]
    if (note and family and not drive and state.profile.children is None
            and not any(o.group == FAMILY_GROUP for o in cars)):
        lines.append(f"אם יש לכם {snapshot.calc.family_min_children} ילדים ומעלה, נפתחים גם רכבי המסלול המשפחתי.")
    return "\n".join(lines)


def options_ask(state: ConversationState, snapshot: RulesSnapshot) -> str:
    cars = [o for o in available_options(snapshot, state) if o.car is not None]
    group = state.profile.group
    if not group or not cars:
        return OPTIONS_ASK
    return f"איזה רכבים להשוות? בקבוצה {group} ומטה יש {len(cars)} רכבים, ואפשר לבחור עד {MAX_OPTIONS}."


def plan_reply(state: ConversationState, snapshot: RulesSnapshot, *, understood: bool,
               just_calculated: bool, matched_car: bool = False, notice: Optional[str] = None,
               rep_opened: bool = False, notice_asks: bool = False, cta_field: Optional[str] = None,
               confirmations: Sequence[str] = (),
               answers: Sequence[tuple[str, str]] = (), follow_up: Sequence[str] = (),
               explain: bool = False, extra: Sequence[str] = (), about_results: bool = False,
               resume: bool = False, light: bool = False, attempted: bool = True,
               open_help: bool = False) -> ReplyPlan:
    """The next reply, from the state only. answers: (FAQ answer, term) pairs; extra: lines that come
    from code (an unanswered question, a redirect) and go right after the answers. resume: the employee only
    made small talk, so the open question is asked again as it was, picked up where it stopped."""
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
    if plan.fixed and state.pending_confirmation is not None:
        # results came with a value still held (a correction next to another change): the held value is the one
        # question of the reply, never hidden behind the results' next step (QA 22/10)
        pending = state.pending_confirmation
        plan.ask.append(held_text(pending))
        plan.cta = call_to_action(state, snapshot)[0]
        return plan
    if plan.fixed and state.stage == Stage.PRESENTED:
        # after results: one relevant next step, its own paragraph (the main reason is in the summary, #9 27/11). With
        # the car buttons on screen the results text already ends with them (#10): no second next step
        ok = [r for r in state.results if r.result.get("status") == "OK" and r.result.get("delta_display") is not None]
        if ok and not car_buttons_shown(state):
            plan.cta = (NEXT_AFTER_RANK if state.ranked_count else NEXT_AFTER_RESULTS)
        return plan
    if about_results and state.stage == Stage.PRESENTED and state.pending_confirmation is None:
        # a question about the results on screen was answered from them (extra): the reply ends with the results'
        # next step, never by asking to choose cars again (C5, 22/10)
        plan.cta = NEXT_AFTER_RANK if state.ranked_count else NEXT_AFTER_RESULTS
        return plan
    cta, _ = call_to_action(state, snapshot)
    plan.cta = brief_cta(cta) if state.brief else cta
    if state.pending_rep_question is not None:
        plan.ask.append(f"אשלח לנציג התחבורה: \"{state.pending_rep_question}\". לשלוח?")
        return plan
    if state.unanswered_question is not None:
        plan.ask.append(unanswered_offer(state, snapshot))   # one call to action: the offer; no flow question
        plan.cta = ""
        return plan
    if state.pending_ambiguity is not None:
        plan.ask.append("לא בטוח שהבנתי: אין לכם רכב צמוד היום, או שיש רכב וההשתתפות שלכם היא 0?")
        return plan
    if state.pending_none_choice:
        plan.ask.append(NONE_QUESTION)          # nothing saved; the buttons say what the "אין" is about (23/11)
        return plan
    if state.pending_clarification is not None:
        plan.ask.append(clarify_role_text(state.pending_clarification.label))
        return plan
    fields = current_group(state)[1]
    if notice_asks and notice:
        plan.ask.append(notice)                        # range problem / conflict: the sentence asks
        if cta_field:
            plan.cta = field_cta(cta_field)            # the call to action answers that same question
        return plan
    if state.pending_confirmation is not None:
        pending = state.pending_confirmation
        plan.ask.append(held_text(pending))
        return plan
    if state.pending_occupation is not None:
        plan.ask.append(confirm_occupation_text(state.pending_occupation.text,
                                                state.pending_occupation.suggestions))
        return plan
    asked_something = bool(answers or extra or follow_up)
    repeat = not understood and not asked_something and fields == state.last_asked
    if not understood and not asked_something and not explain:
        if attempted:
            plan.ask.append(CAR_AGAIN if fields[:1] == ["car"] else NOT_UNDERSTOOD)
        else:
            # nothing in it could answer the question (a remark, or gibberish: without a model the two look the
            # same): never "לא הצלחתי להבין את התשובה"; the rest of the not-understood flow stays
            plan.ask.append(UNSURE)
    present = [f for f in follow_up if f in fields]
    if present:
        plan.follow_up.append(follow_up_text(present, snapshot))
    rest = [f for f in fields if f not in present]
    # a turn that gave no data: answered first, then a light question. A compound question is narrowed to its first
    # field; a question about one field keeps its own wording (it may carry context: an unknown value, the payslip)
    # The field the employee was asked before this turn comes first (16/11: after "מה זה קרן השתלמות?" the pension
    # question that was waiting became a study-fund question, and the next "5" went to the study fund).
    pending = _ASKED_FIELD.get(state.asked_field or "")
    focus = ((pending if pending in paced(state, rest) else _first_field(paced(state, rest)))
             if light and len(paced(state, rest)) > 1 else None)
    if open_help:
        plan.ask.append(OPEN_HELP)                     # small talk at the very start: an open question, not a form
        plan.cta = OPEN_HELP_CTA
    elif explain or repeat:
        compound = len(paced(state, fields)) > 1 and _is_q2(fields)
        acked = any(a.startswith(SAVED_STARTS) for a in plan.ack)
        line = ((still_missing_text(paced(state, fields)) if acked else progress_text(state, fields))
                if repeat and not explain and compound
                else explain_text(state, snapshot, [focus] if focus else fields))
        # root cause of the duplicate bubble: the parser's notice (already in plan.ack) can be the same
        # progress line; do not add it a second time to plan.ask
        if line and line not in plan.ack:
            plan.ask.append(line)
        if repeat and state.misses >= 1:              # missed again: never the same reply twice
            one = placeholder_text(state, snapshot).removeprefix("למשל: ").split(", ")[0]
            plan.ask.append(f"אפשר גם לענות על דבר אחד בכל פעם, למשל: {one}.")
    elif rest:
        plan.ask.append(question(focus, state, snapshot) if focus else group_question(state, snapshot, skip=present))
    if light and not explain and not open_help and plan.cta:
        # no example on a light turn; an explanation ("מה?", feedback) keeps it: it shows how to answer
        plan.cta = brief_cta(call_to_action(state, snapshot, [focus] if focus else None)[0])
    plan.ask = [a for a in plan.ask if a]
    _vary_repeat(plan, state, snapshot, [focus] if focus else fields, resume=resume)
    return plan


# state.asked_field (a save_profile argument) → the missing item it asks
_ASKED_FIELD = {**PROFILE_ARGS, "current_copay": "copay", "current_use_value": "use_value",
                "current_maintenance": "maintenance", "car": "car"}


def resume_question(state: ConversationState, snapshot: RulesSnapshot, asked: Optional[str]) -> str:
    """"נמשיך מאיפה שעצרנו: <the question>" for the field that was waiting (state.asked_field), while it is still
    missing; "" otherwise. After an explanation the conversation returns exactly to it (16/11)."""
    field = _ASKED_FIELD.get(asked or "")
    p = state.profile
    still = (field in open_profile(p) if field in PROFILE_ARGS.values()
             else field in missing_current_car(state.current))
    return RESUME + question(field, state, snapshot) if field and still else ""

UNSURE = "לא בטוח שהבנתי למה התכוונתם."
OPEN_HELP = "איך אפשר לעזור לכם היום עם רכב החברה?"
OPEN_HELP_CTA = "אפשר לכתוב כאן כל שאלה, או להתחיל מקבוצת הזכאות שלכם."


def _first_field(fields: Sequence[str]) -> Optional[str]:
    """The one field a light turn asks: the same order the call to action uses."""
    if not fields:
        return None
    return next((f for f in ("car", "copay", "opt_out_occupation", "options") if f in fields), fields[0])


RESUME = "נמשיך מאיפה שעצרנו: "
_EXAMPLE_RE = re.compile(r",\s*למשל:\s*(?:\"[^\"]*\"|[^.\"]*)")


def brief_cta(cta: str) -> str:
    """"יותר קצר" (state.brief): the call to action without its example; the composer's placeholder keeps it.
    "כתבו כאן, למשל: "קבוצה ג…"." -> "כתבו כאן."; the link and any other sentence stay."""
    return _EXAMPLE_RE.sub("", cta)


def _vary_repeat(plan: ReplyPlan, state: ConversationState, snapshot: RulesSnapshot, fields: Sequence[str],
                 resume: bool = False) -> None:
    """The same question is never asked verbatim twice in a row: narrowed to what is still missing, or
    in simpler words. After small talk it is the same question, picked up where it stopped (30/10): "עדיין
    חסרים לי…" there read as if the employee had failed to answer."""
    if not plan.ask or plan.ask[-1] != state.last_ask:
        return
    if resume and not plan.ask[-1].startswith(RESUME):
        plan.ask[-1] = RESUME + plan.ask[-1]
        return
    # what was saved is said once: after this turn's own "רשמתי"/"עדכנתי" line, only what is still missing (15/11:
    # "עדכנתי: שכר בסיס 19,000 ₪ במקום 18,500 ₪." was followed by "רשמתי: שכר בסיס 19,000 ₪. עדיין חסרים לי…")
    acked = any(line.startswith(SAVED_STARTS) for line in plan.ack)
    first = (progress_text(state, fields) if len(paced(state, fields)) > 1 and not acked
             else still_missing_text(paced(state, fields)))
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


def inquiry_next_text(mail: bool, verified: bool, address_given: bool, simulated: bool = False) -> str:
    """After an inquiry is sent: where the answer will appear, and — only when mail is really configured — what the
    email will do. Never claims an email was sent (it is queued; delivery happens after this reply)."""
    if mail and simulated:
        # a local test server with the fake mail backend: no real email is ever sent (31/10)
        return "התשובה תופיע כאן בשיחה. בסביבת הבדיקות הזו לא נשלח מייל אמיתי."
    if mail and verified:
        return "התשובה תופיע כאן בשיחה, ונעדכן אתכם גם באימייל כשהנציג יענה."
    if mail and address_given:
        return ("התשובה תופיע כאן בשיחה. ביקשנו לשלוח לכתובת שלכם קישור לאימות. "
                "אחרי האימות נעדכן אתכם גם באימייל כשהנציג יענה.")
    return "התשובה תופיע כאן בשיחה."


# what an inquiry the system opened by itself is about (no words of the employee's to show): the employee's screen
INQUIRY_PURPOSE = {
    "NEW_CAR_COPAY": "בקשה לאשר את סכום ההשתתפות החודשי.",
    "ELIGIBILITY_GROUP": "בירור קבוצת הזכאות שלכם.",
    "CURRENT_COPAY": "בירור ההשתתפות ברכב שלכם היום.",
    "CURRENT_USE_VALUE": "בירור שווי השימוש ברכב שלכם היום.",
    "MAINTENANCE_AMOUNT": "בירור סכום האחזקה.",
    "OCCUPATION_OPT_OUT": "בירור אפשרות הוויתור על רכב.",
}
INQUIRY_PURPOSE_DEFAULT = "בירור מול נציג התחבורה."


def rep_request_text(question: Optional[str]) -> str:
    """"אני רוצה לדבר עם נציג": the offer, at once, with the question still open (29/10). Nothing the employee
    already gave is asked again here; the conversation goes on from where it was after this."""
    if question:
        return (f"אפשר להעביר לנציג התחבורה את השאלה: \"{question}\". התשובה תגיע לכאן, "
                "והפרטים שכבר מסרתם נשמרים. רוצים שאשאל אותו?")
    return ("אפשר לפנות לנציג התחבורה. כתבו מה תרצו לשאול, והתשובה תגיע לכאן. "
            "הפרטים שכבר מסרתם נשמרים.")


def aside_follow_up(state: ConversationState, snapshot: RulesSnapshot) -> str:
    """The one line after a small-talk or off-topic message (24/11): the representative step waiting for the employee,
    else a question on screen again (its buttons stay: nothing changed), else after the results the next step (an open
    question with the representative first). Never the results, never the sources text."""
    if state.pending_rep_question is not None:
        return ASIDE_REP_DRAFT
    if state.unanswered_question is not None:
        return ASIDE_REP_OFFER
    if next_stage(state) == Stage.WAITING_FOR_REP:
        return ASIDE_REP_WAITING
    held = (state.pending_value_choice, state.pending_ambiguity, state.pending_none_choice, state.pending_confirmation,
            state.pending_occupation, state.pending_clarification, state.rep_copay_offer)
    if any(h is not None for h in held):
        # a question of code's own, with its buttons (an ambiguity, a value to confirm…): the last reply asked it
        return state.last_ask or group_question(state, snapshot)
    if state.stage == Stage.PRESENTED and state.results:
        if any(q.answer is None for q in state.general_questions.values()):
            return ASIDE_REP_OPEN
        if car_buttons_shown(state):
            return results_pointer()       # the same pointer as the results text, by the buttons' names (#10)
        return ASIDE_AFTER_RANK if state.ranked_count else NEXT_AFTER_RESULTS
    return group_question(state, snapshot) or state.last_ask       # the flow's question on screen
