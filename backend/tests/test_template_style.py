"""Hebrew style guide over the templates: short lines, no "·" chains, directions in words, no raw
negative amounts. The results text covers every case in at most two (plain) or three lines."""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from app.modules.conversation import templates as T
from app.modules.conversation.stage import ConversationState, OptionResult

MAX_LINE = 120


def _item(label, delta, status="OK", pending=False, reason="סיבה קצרה"):
    result = {"status": status, "reason": reason, "delta_display": None if delta is None else str(delta),
              "copay_pending": pending, "drivers": []}
    return OptionResult(option_id=label, label=label, result=result)


def text(*items):
    return T.results_text(ConversationState(results=list(items)))


def assert_style(t):
    for line in t.splitlines():
        assert len(line) <= MAX_LINE, line
    assert "·" not in t and "שינוי של" not in t
    assert not re.search(r"(?<![\w-])-\d[\d,]* ₪", t), t           # "-923 ₪" (a raw negative amount)
    assert not re.search(r"[+−-]\d", t.replace("ב-", "")), t


def test_all_up():
    t = text(_item("A", 100), _item("B", 300), _item("C", 200))
    assert t.splitlines() == ["בדקתי 3 רכבים. בכולם הנטו שלך יגדל, הכי הרבה ב-B. הנטו שלך יגדל ב-300 ₪ בחודש."]
    assert_style(t)


def test_all_down_names_the_least_bad():
    t = text(_item("A", -923), _item("B", -1200), _item("C", -1500))
    assert t == "בדקתי 3 רכבים. בכולם הנטו שלך יקטן, הכי פחות ב-A. הנטו שלך יקטן ב-923 ₪ בחודש."
    assert_style(t)


def test_mixed_names_the_best():
    t = text(_item("A", -50), _item("B", 70))
    assert t == "בדקתי 2 רכבים. הכי משתלם B. הנטו שלך יגדל ב-70 ₪ בחודש."
    assert_style(t)


def test_zero_and_single():
    assert text(_item("A", 0), _item("B", 0)) == "בדקתי 2 רכבים. בכולם הנטו שלך לא ישתנה."
    assert text(_item("A", 0)) == "בדקתי את A. הנטו שלך לא ישתנה."
    assert text(_item("A", -5)) == "בדקתי את A. הנטו שלך יקטן ב-5 ₪ בחודש."


def test_three_options_with_pending_copay_are_two_lines():
    t = text(_item("A", 100, pending=True), _item("B", -300, pending=True), _item("C", 5))
    assert len(t.splitlines()) == 2 and t.splitlines()[1] == "ממתין לאישור נציג על ההשתתפות."
    assert_style(t)


def test_not_eligible_and_incomplete_get_one_short_line_each():
    t = text(_item("A", 100), _item("B", None, "NOT_ELIGIBLE", reason="קבוצה גבוהה"),
             _item("C", None, "INCOMPLETE", reason="חסר שכר"))
    lines = t.splitlines()
    assert lines[1] == "B לא זמין לך: קבוצה גבוהה" and lines[2] == "C: חסר מידע לחישוב. חסר שכר"
    assert_style(t)


def test_only_bad_options():
    assert text(_item("B", None, "NOT_ELIGIBLE", reason="קבוצה גבוהה")) == "B לא זמין לך: קבוצה גבוהה"


@pytest.mark.parametrize("name", [n for n in dir(T) if n.isupper() and isinstance(getattr(T, n), str)])
def test_constant_templates_follow_the_style(name):
    t = getattr(T, name)
    assert "·" not in t and "שינוי של" not in t
    assert all(len(line) <= 200 for line in t.splitlines())


def test_net_change_wording_is_future_tense():
    assert T.net_change(Decimal("-923")) == "הנטו שלך יקטן ב-923 ₪ בחודש"
    assert T.net_change(Decimal("250")) == "הנטו שלך יגדל ב-250 ₪ בחודש"
    assert T.net_change(0) == "הנטו שלך לא ישתנה"
