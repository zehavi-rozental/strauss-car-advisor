"""אין מבוי סתום: (1) שאלה שנשאלת פעם שנייה ברצף לא חוזרת באותו ניסוח — מגיעה עם כפתורים ודוגמה;
(2) כל הודעת בוט שאינה תוצאה (ואינה המתנה לנציג) כוללת expects שאינו none."""
from __future__ import annotations

import pytest

from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, CTA_STARTS

GIBBERISH = "בלה בלה בלה"
STEPS = [
    ("start", None),
    ("send", "מנהלת מוצר"),                   # Q1 → שכר בסיס
    ("send", "18,500"),                       # Q2
    ("send", GIBBERISH),                      # לא הובן — Q2 שוב
    ("send", GIBBERISH),
    ("send", "אין עמלות, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"),   # Q3
    ("send", GIBBERISH),
    ("send", GIBBERISH),
    ("send", "יש לי קורולה קרוס ואני משתתפת ב-300"),
    ("send", GIBBERISH),                      # אפשרויות
    ("send", GIBBERISH),
    ("send", "אטו 3"),
]


def service(sessions):
    return ConversationService(sessions, RulesStore(CONFIG), LLMChain())


def run(sessions):
    svc = service(sessions)
    turns, token, cid = [], None, None
    for kind, text in STEPS:
        if kind == "start":
            turn, token = svc.start()
            cid = turn.conversation_id
        else:
            turn = svc.send(cid, token, text)
        turns.append((text, turn))
    return turns


def test_every_non_result_reply_offers_a_way_forward(sessions):
    for text, turn in run(sessions):
        if turn.results or turn.stage == Stage.WAITING_FOR_REP:
            continue
        assert turn.expects["type"] != "none" and turn.expects["fields"], f"dead end after {text!r}: {turn.reply}"


def test_a_question_repeated_right_away_comes_with_buttons_and_an_example(sessions):
    turns = run(sessions)
    checked = 0
    for (_, first), (text, second) in zip(turns, turns[1:]):
        if text != GIBBERISH or first.expects["fields"] != second.expects["fields"]:
            continue
        # אותה שאלה פעם שנייה (או שלישית) ברצף: לא אותו ניסוח — במילים פשוטות, ועם דוגמה
        assert "במילים פשוטות" in second.reply and "למשל" in second.reply, second.reply
        assert second.reply != first.reply
        if not first.reply.startswith(("לא הצלחתי", "לא זיהיתי")):
            assert second.reply != first.reply
        checked += 1
    assert checked >= 4


@pytest.mark.parametrize("field", ["base", "pension_percent", "car", "options"])
def test_every_field_has_an_exact_example(field):
    from app.modules.conversation.templates import EXAMPLES
    assert field in EXAMPLES and EXAMPLES[field]


def test_every_non_result_reply_ends_with_exactly_one_call_to_action(sessions):
    for text, turn in run(sessions):
        if turn.results or turn.stage == Stage.WAITING_FOR_REP:
            continue
        paragraphs = turn.reply.split("\n\n")
        assert paragraphs[-1].startswith(CTA_STARTS), f"no call to action after {text!r}: {turn.reply}"
        assert sum(p.startswith(CTA_STARTS) for p in paragraphs) == 1, turn.reply


def test_every_question_has_a_placeholder_with_an_example(sessions):
    seen = set()
    for _, turn in run(sessions):
        if turn.results or turn.stage == Stage.WAITING_FOR_REP:
            continue
        seen.add(turn.expects["field"])
        assert turn.expects["placeholder"].startswith("למשל: ") and len(turn.expects["placeholder"]) > 8


def test_closed_questions_say_choose_or_write(sessions):
    """Buttons only for closed questions, and then the text says "בחרו … או כתבו"."""
    for _, turn in run(sessions):
        if turn.expects.get("closed"):
            assert turn.reply.split("\n\n")[-1].startswith("בחרו") and "כתבו" in turn.reply
