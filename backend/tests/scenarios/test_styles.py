"""ששת העובדים לדוגמה × שלושה סגנונות כתיבה, מקצה לקצה דרך ה-API.

העובד המדומה עונה רק על מה שהשאלה הנוכחית מבקשת (expects), לפי הנתונים שלו מקובץ הייחוס:
- buttons: כל מה שיש לו כפתור — בכפתור; מספר בלי כפתור — המספר לבד (או עם תווית בשאלה מקובצת).
- short:   "אין", "לא ידוע", מספרים ושמות קצרים. ההשתתפות היום תמיד "לא ידוע" — הנציג עונה.
- free:    משפטים מלאים עם כל מה שהשאלה ביקשה בהודעה אחת.

בכל שיחה: מגיעים לתוצאה (או לשאלת נציג מוצדקת שנענית), אף תשובה לא חוזרת יותר מפעמיים, אין "לא
זיהיתי" אחרי כפתור, כל הודעה שאינה תוצאה או המתנה לנציג מציעה המשך (expects), והנטו זהה לייחוס.

ברירת המחדל: בלי מודל שפה (תבניות + קורא). מול הספק האמיתי:  STYLES_LLM=live pytest tests/scenarios
"""
from __future__ import annotations

import os
import time
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import pytest

from app.modules.calculation.public import OptOutState
from app.modules.conversation.public import LLMChain, build_llm_chain
from app.modules.rules.public import load_snapshot, parse_reference_state
from app.shared.settings import Settings

from ..conftest import CONFIG, make_client

SNAP = load_snapshot(CONFIG)
LIVE = os.environ.get("STYLES_LLM") == "live"
TURN_PAUSE_S = float(os.environ.get("STYLES_TURN_PAUSE_S", "0"))
MAX_TURNS = 20
REP = {"X-Rep-Token": "styles-test"}
STYLES = ("buttons", "short", "free")
Q2 = ("commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children")
ZERO_OK = {"commissions", "bonus", "study_fund_percent", "children"}
# איך עובד כותב את התפקיד (בטבלה: צורת לוכסן)
TYPED_OCCUPATION = {"מנהל/ת מוצר": "מנהלת מוצר", "נציג/ת מכירות שטח": "נציג מכירות שטח",
                    "מפתח/ת תוכנה": "מפתחת תוכנה", "מנהל/ת אזור מכירות": "מנהל אזור מכירות",
                    "מנהל/ת פיתוח": "מנהלת פיתוח", "מנהל/ת מחלקה": "מנהל מחלקה"}


@dataclass(frozen=True)
class Employee:
    id: str
    occupation: str
    values: dict[str, Decimal]           # שדות הפרופיל + copay / maintenance
    car: Optional[str]                   # מזהה קטלוג; None = אין רכב היום
    alternative: str                     # מזהה קטלוג או OPT_OUT
    net_today: Decimal
    net_alternative: Decimal

    def typed_car(self, code: str) -> str:
        return "ויתור" if code == "OPT_OUT" else SNAP.extensions.car_aliases[code][0]

    @property
    def alt_label(self) -> str:
        return "ויתור" if self.alternative == "OPT_OUT" else _catalog_label(self.alternative)


def _catalog_label(code: str) -> str:
    return next(c.label for c in SNAP.catalog if c.catalog_code == code)


def _code(label: str) -> str:
    return next(c.catalog_code for c in SNAP.catalog if c.label == label)


def _employees() -> list[Employee]:
    refs = {r.employee_id: r for r in SNAP.reference_results}
    out = []
    for e in SNAP.reference_employees:
        ref = refs[e.employee_id]
        current = parse_reference_state(SNAP, e.current_car, current_copay=e.current_copay, for_current=True)
        alternative = parse_reference_state(SNAP, ref.alternative_label)
        values = {"base": e.base, "commissions": e.commissions, "bonus": e.bonus, "pension_percent": e.pension_percent,
                  "study_fund_percent": e.study_fund_percent, "credit_points": e.credit_points,
                  "children": Decimal(e.children)}
        if isinstance(current, OptOutState):
            car, values["maintenance"] = None, current.maintenance
        else:
            car, values["copay"] = _code(current.label), e.current_copay
        out.append(Employee(e.employee_id, e.occupation, values, car,
                            "OPT_OUT" if isinstance(alternative, OptOutState) else _code(alternative.label),
                            ref.net_today, ref.net_alternative))
    return out


EMPLOYEES = _employees()


# ---------- ניסוח ----------

def num(v: Decimal, commas: bool = False) -> str:
    v = v.normalize()
    return f"{v:,f}" if commas else f"{v:f}"


def labelled(field: str, v: Decimal) -> str:
    zero = v == 0
    return {
        "base": f"שכר בסיס {num(v, True)}",
        "commissions": "אין עמלות" if zero else f"עמלות {num(v, True)} ש\"ח",
        "bonus": "אין בונוס" if zero else f"בונוס {num(v, True)} ש\"ח",
        "pension_percent": f"פנסיה {num(v)}%",
        "study_fund_percent": "אין קרן השתלמות" if zero else f"קרן השתלמות {num(v)}%",
        "credit_points": f"{num(v)} נקודות זיכוי",
        "children": "אין ילדים" if zero else f"{num(v)} ילדים",
    }[field]


def fields_of(expects: dict[str, Any]) -> list[dict[str, Any]]:
    return expects.get("fields") or ([expects] if expects.get("type") not in (None, "none") else [])


def clarify_answer(emp: Employee, reply: str, short: bool) -> str:
    """"<רכב> הוא הרכב שלך היום, או חלופה?" — לפי הרכב שבשאלה."""
    alternative = emp.alternative != "OPT_OUT" and _catalog_label(emp.alternative).split(" (")[0] in reply
    if alternative:
        return "חלופה" if short else "זו חלופה שאני רוצה לבדוק"
    return "הרכב שלי" if short else "זה הרכב שלי היום"


def answer(style: str, emp: Employee, expects: dict[str, Any], reply: str, rephrase: bool = False) -> dict[str, Any]:
    original = style
    if rephrase and style == "free":
        style = "short"                      # לא הובן: אחרי הדוגמה כותבים קצר
    items = fields_of(expects)
    names = [f["field"] for f in items]
    grouped = len(items) > 1
    v = emp.values

    def choice(id_: str, field: str) -> dict[str, Any]:
        return {"choice": id_, "field": field} if grouped else {"choice": id_}

    first = names[0] if names else None
    if first == "confirmation":
        return {"choice": "yes"} if style == "buttons" else {"text": "כן"}
    if first == "clarification":
        if style == "buttons":
            return {"choice": "alternative" if "חלופה" in clarify_answer(emp, reply, True) else "mine"}
        return {"text": clarify_answer(emp, reply, style == "short")}

    if style == "buttons":
        for f in items:
            name = f["field"]
            ids = {c["id"] for c in f["choices"]} | {q["id"] for q in f["quick_replies"]}
            if name in ("occupation", "opt_out_occupation") and emp.occupation in ids:
                return choice(emp.occupation, name)
            if name == "car":
                return choice(emp.car or "no_car", name)
            if name == "options" and emp.alternative in ids:
                return {"choices": [emp.alternative], "field": name} if grouped else {"choices": [emp.alternative]}
            if name in v and v[name] == 0 and "none" in ids:
                return choice("none", name)
        for f in items:
            name = f["field"]
            if name in v:
                return {"text": labelled(name, v[name]) if grouped and name in Q2 + ("base",) else num(v[name])}
        raise AssertionError(f"buttons: nothing to answer for {names}")

    if style == "short":
        if "occupation" in names or "opt_out_occupation" in names:
            return {"text": TYPED_OCCUPATION[emp.occupation]}
        if "car" in names:
            return {"text": emp.typed_car(emp.car) if emp.car else "אין רכב"}
        if "copay" in names:
            return {"text": "לא ידוע" if original == "short" else num(v["copay"])}
        if "options" in names and "children" in names and v["children"] >= 4:
            return {"text": labelled("children", v["children"])}     # המסלול המשפחתי תלוי במספר הילדים
        if "options" in names:
            return {"text": emp.typed_car(emp.alternative)}
        missing = [n for n in names if n in v]
        if len(missing) == 1:
            n = missing[0]
            return {"text": "אין" if v[n] == 0 and n in ZERO_OK else num(v[n])}
        if missing and all(v[n] == 0 for n in missing if n in ZERO_OK) and any(n in ZERO_OK for n in missing):
            return {"text": "אין"}
        if missing:
            return {"text": labelled(missing[0], v[missing[0]])}
        raise AssertionError(f"short: nothing to answer for {names}")

    # free: משפט אחד עם כל מה שהשאלה ביקשה
    parts: list[str] = []
    if "occupation" in names or "group" in names or "opt_out_occupation" in names:
        parts.append(f"אני {TYPED_OCCUPATION[emp.occupation]}")
    if "base" in names:
        parts.append(labelled("base", v["base"]))
    if "car" in names:
        parts.append(f"יש לי {emp.typed_car(emp.car)} ואני משתתפת ב-{num(v['copay'])} ש\"ח בחודש" if emp.car
                     else f"אין לי רכב, אני מקבלת אחזקה {num(v['maintenance'], True)} ש\"ח בחודש")
    elif "copay" in names:
        parts.append(f"אני משתתפת היום ב-{num(v['copay'])} ש\"ח בחודש")
    if "maintenance" in names:
        parts.append(f"אני מקבלת אחזקת רכב {num(v['maintenance'], True)} ש\"ח בחודש")
    if "options" in names:
        parts.append("אני רוצה לוותר על הרכב" if emp.alternative == "OPT_OUT"
                     else f"אני רוצה לבדוק את {emp.typed_car(emp.alternative)}")
    parts += [labelled(n, v[n]) for n in names if n in Q2]
    if not parts:
        raise AssertionError(f"free: nothing to answer for {names}")
    return {"text": ", ".join(parts)}


# ---------- השיחה ----------

def _alt_result(results: list[dict[str, Any]], emp: Employee) -> Optional[dict[str, Any]]:
    for r in results:
        r = r.get("result", r)                     # נטו בתוך result (GET) או שטוח (POST)
        if r.get("alternative") and r["alternative"]["label"].startswith(emp.alt_label.split(" (")[0]):
            return r
    return None


def run_conversation(style: str, emp: Employee) -> dict[str, Any]:
    llm = build_llm_chain(Settings()) if LIVE else LLMChain()
    from ..conftest import build_engine
    client = make_client(build_engine(), llm=llm, rep_access_token=REP["X-Rep-Token"])
    body = client.post("/api/conversations").json()
    cid = body["conversation_id"]
    log: list[str] = []
    replies: Counter[str] = Counter()
    rep_answered: list[str] = []
    failed = False
    for _ in range(MAX_TURNS):
        expects, stage = body["expects"], body["stage"]
        result = _alt_result(body.get("results") or [], emp)
        if result is not None:
            return {"result": result, "log": log, "rep": rep_answered}
        if stage == "WAITING_FOR_REP":
            questions = client.get("/api/rep/questions?status=OPEN", headers=REP).json()
            required = [q for q in questions if q["mode"] == "REQUIRED"]
            assert required, f"{emp.id}/{style}: waiting with no required question\n" + "\n".join(log)
            for q in required:
                # מוצדקת: רק ההשתתפות של היום, אחרי "לא ידוע" (סגנון קצר)
                assert q["missing_field"] == "CURRENT_COPAY" and style == "short", (
                    f"{emp.id}/{style}: unjustified rep question {q['missing_field']}\n" + "\n".join(log))
                r = client.post(f"/api/rep/questions/{q['id']}/answer", headers=REP,
                                json={"value": num(emp.values["copay"])})
                assert r.status_code == 200, r.text
                rep_answered.append(q["missing_field"])
            body = client.get(f"/api/conversations/{cid}/updates?after=0").json()
            continue
        fields = fields_of(expects)
        assert fields, f"{emp.id}/{style}: dead end — no expects at {stage}\n" + "\n".join(log)
        message = answer(style, emp, expects, body.get("reply", ""), rephrase=failed)
        r = client.post(f"/api/conversations/{cid}/messages", json=message)
        assert r.status_code == 200, f"{emp.id}/{style}: {message} → {r.status_code} {r.text}\n" + "\n".join(log)
        body = r.json()
        reply = body["reply"]
        log.append(f"  > {message}\n  < {reply[:160]!r}")
        replies[reply] += 1
        was_failed, failed = failed, "לא זיהיתי" in reply or "לא הצלחתי" in reply
        if was_failed and failed:                   # שאלה שחוזרת פעם שנייה: כפתורים או דוגמה, לא אותו ניסוח
            assert "כפתורים" in reply and "לדוגמה" in reply, (
                f"{emp.id}/{style}: a repeated question without buttons and an example\n" + "\n".join(log))
        assert replies[reply] <= 2, f"{emp.id}/{style}: the same reply 3 times\n" + "\n".join(log)
        if "choice" in message or "choices" in message:
            assert "לא זיהיתי" not in reply, f"{emp.id}/{style}: 'לא זיהיתי' after a button\n" + "\n".join(log)
        if TURN_PAUSE_S:
            time.sleep(TURN_PAUSE_S)
    raise AssertionError(f"{emp.id}/{style}: no result after {MAX_TURNS} turns\n" + "\n".join(log))


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("emp", EMPLOYEES, ids=lambda e: e.id)
def test_style(emp: Employee, style: str):
    out = run_conversation(style, emp)
    r = out["result"]
    assert r["status"] == "OK", f"{emp.id}/{style}: {r['status']} {r['reason']}\n" + "\n".join(out["log"])
    assert (Decimal(r["today"]["net_display"]), Decimal(r["alternative"]["net_display"])) == (
        emp.net_today, emp.net_alternative), f"{emp.id}/{style}\n" + "\n".join(out["log"])
