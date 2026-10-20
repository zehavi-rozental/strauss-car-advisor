"""Grounding (conversation/grounding.py): a value the LLM saves must be said by the employee. The number words
("שמונה עשר וחצי אלף") are tested explicitly, and every offline eval case is replayed: nothing a case says is
refused."""
from __future__ import annotations

from decimal import Decimal as D

import pytest
import yaml

from app.modules.conversation.grounding import (
    grounded_number, group_named, has_zero_cue, occupation_named, option_named, said_numbers, word_numbers,
    zero_grounded)
from app.modules.rules.public import load_snapshot

from .conftest import CONFIG

SNAP = load_snapshot(CONFIG)
ZERO = SNAP.extensions.zero_cues


# ---------- Hebrew number words ----------

@pytest.mark.parametrize("text, value", [
    ("שמונה עשר וחצי אלף", 18500), ("מרוויחה שמונה עשר וחצי אלף ברוטו", 18500), ("עשרים אלף", 20000),
    ("חמש עשרה אלף", 15000), ("שמונה עשרה אלף", 18000), ("שתים עשרה אלף", 12000), ("אחד עשר אלף", 11000),
    ("שלושים ושניים אלף", 32000), ("עשרים וחמישה אלף", 25000), ("שלוש מאות", 300), ("ארבע מאות וחמישים", 450),
    ("מאתיים", 200), ("שני ילדים", 2), ("שלושה ילדים", 3), ("ארבעה", 4), ("אחד", 1), ("שתי", 2),
    ("שני וחצי", D("2.5")), ("שש", 6), ("שש אחוז", 6), ("עשרה אחוז", 10), ("אלף", 1000), ("אלפיים", 2000),
    ("שלושת אלפים", 3000), ("חמישה אלפים", 5000), ("שמונה-עשר וחצי אלף", 18500), ("ושמונה עשר וחצי אלף", 18500),
])
def test_hebrew_number_words_are_read(text, value):
    assert grounded_number(value, text), (text, word_numbers(text))


def test_number_words_are_not_a_blank_cheque():
    assert not grounded_number(25000, "שמונה עשר וחצי אלף")
    assert not grounded_number(4, "שני ילדים")
    assert not grounded_number(6, "שלום, מה שלומך")
    assert word_numbers("שלום מה נשמע") == set()


# ---------- digits ----------

@pytest.mark.parametrize("text, value", [
    ("שכר בסיס 18,500", 18500), ("18500 ברוטו", 18500), ("18.5 ברוטו", 18500), ("מרוויחה 18 וחצי ברוטו", 18500),
    ("בערך 20 אלף", 20000), ("20K", 20000), ("פנסיה 6%", 6), ("6 אחוז", 6), ("קרן השתלמות 2.5%", D("2.5")),
    ("2.75 נקודות זיכוי", D("2.75")), ("2 וחצי נקודות", D("2.5")), ("18,500 ₪", 18500), ("משלמת 300 ש\"ח", 300),
])
def test_digits_ground_the_value(text, value):
    assert grounded_number(value, text)


def test_a_bare_small_number_never_grounds_a_thousand():
    assert not grounded_number(2000, "יש לי 2 ילדים")           # the hole the audit found
    assert not grounded_number(18000, "18")                     # "18" is asked (18,000?), never multiplied
    assert not grounded_number(34, "מה שלומך")
    assert not grounded_number("abc", "abc 3")


# ---------- zero cues ----------

@pytest.mark.parametrize("text", ["אין עמלות ואין בונוס", "בלי בונוס", "ללא עמלות", "ואין לי ילדים", "אפס ילדים",
                                  "ילדים: 0", "אינני משתתף", "לא משלמת עליו", "עמלות 0", "לא משתתפת"])
def test_a_said_zero_is_a_zero(text):
    assert has_zero_cue(text, ZERO)


@pytest.mark.parametrize("text", ["שלום", "תודה", "לא יודע", "לא", "מה זה פנסיה?", "יש לי 2 ילדים", "כן"])
def test_a_zero_needs_a_cue(text):
    assert not has_zero_cue(text, ZERO)


@pytest.mark.parametrize("field, text, ok", [
    ("commissions", "אין עמלות ואין בונוס", True), ("bonus", "אין עמלות ואין בונוס", True),
    ("study_fund_percent", "אין עמלות ואין בונוס", False),                  # a cue for other fields
    ("children_under_23", "אין עמלות ואין בונוס", False),
    ("children_under_23", "ואין לי ילדים", True), ("children_under_23", "ילדים: 0", True),
    ("study_fund_percent", "אין קרן השתלמות", True), ("study_fund_percent", "בלי השתלמות", True),
    ("commissions", "אין", True), ("bonus", "אין לי", True), ("children_under_23", "בלי", True),   # a bare "אין"
    ("pension_percent", "אין", False), ("credit_points", "אין", False),    # never assumed from a bare "אין"
    ("pension_percent", "פנסיה 0", True), ("credit_points", "אפס נקודות זיכוי", True),
    ("current_copay", "לא משלמת עליו", True), ("current_copay", "אינני משתתף", True),
    ("current_copay", "אין עמלות", False), ("commissions", "ללא בונוס", False),
    ("commissions", "שלום", False), ("commissions", "לא יודע", False),
])
def test_a_zero_is_grounded_per_field(field, text, ok):
    assert zero_grounded(field, text, ZERO) is ok


# ---------- group, occupation, alternatives ----------

def test_group_is_named():
    order, family = SNAP.calc.group_order, "משפחתי"
    for text in ("קבוצה ג", "אני בקבוצה ג, מרוויחה 18", "ב-ג", "ג", "קבוצת ד"):
        assert group_named(text, order, family), text
    assert not group_named("שלום", order, family) and not group_named("מה אתם עושים", order, family)


def test_occupation_is_named_loosely_but_not_invented():
    assert occupation_named("מנהל/ת מוצר", "אני מנהלת מוצר", "מנהל/ת מוצר")
    assert occupation_named("מנהל/ת מוצר", "מנהלת בכירה למוצר", None)
    assert not occupation_named("מנהל/ת מוצר", "שלום", None)
    assert not occupation_named("מנהל/ת מוצר", "מה שלומך היום", None)


def test_alternatives_are_named_numbered_or_ranked():
    rank = ("הכי משתלם", "המלצ")
    assert option_named(["CAR-10"], {"CAR-10"}, "אטו 3", rank)
    assert option_named(["CAR-10", "CAR-06"], {"CAR-10", "CAR-06"}, "אטו 3 ו-MG4", rank)
    assert option_named(["CAR-10"], set(), "מספר 3 ברשימה", rank)               # a number in the list shown
    assert option_named(["CAR-10"], set(), "מה הכי משתלם לי?", rank)
    assert not option_named(["CAR-10"], set(), "מה שלומך?", rank)
    assert not option_named(["CAR-10"], {"CAR-06"}, "אם ג'י 4", rank)           # the model chose another car


# ---------- replay of every offline eval case: nothing the employee said is refused ----------

EVAL = yaml.safe_load(open("tests/eval/conversations.yaml", encoding="utf-8"))["cases"]
FIELD_TEXT = {"base": "base_salary", "commissions": "commissions", "bonus": "bonus", "pension_percent": "pension_percent",
              "study_fund_percent": "study_fund_percent", "credit_points": "credit_points", "children": "children_under_23",
              "copay": "current_copay"}


@pytest.mark.parametrize("case", [c for c in EVAL if c.get("saved")], ids=lambda c: c["id"])
def test_every_value_an_eval_case_expects_is_grounded_in_what_the_employee_wrote(case):
    texts = [t for t in case["turns"] if isinstance(t, str)]
    for field, expected in case["saved"].items():
        assert field in FIELD_TEXT, field
        arg = FIELD_TEXT[field]
        ok = (any(zero_grounded(arg, t, ZERO) for t in texts) if D(str(expected)) == 0
              else any(grounded_number(expected, t) for t in texts))
        assert ok, f"{case['id']}: {field}={expected} is not grounded in {texts}"


def test_every_reference_employee_message_grounds_all_their_values():
    from tests.eval.test_conversation_eval import employee_turns
    for case in [c for c in EVAL if "employee" in c]:
        msgs, emp = employee_turns(case["employee"])
        joined = " ".join(msgs)
        for name in ("base", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children"):
            value = getattr(emp, name, None)
            if value is None:
                continue
            arg = {"base": "base_salary", "children": "children_under_23"}.get(name, name)
            ok = zero_grounded(arg, joined, ZERO) if D(str(value)) == 0 else grounded_number(value, joined)
            assert ok, f"{case['id']}: {name}={value} not grounded in {msgs}"
