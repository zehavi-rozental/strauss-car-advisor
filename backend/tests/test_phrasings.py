"""סט הניסוחים במצב תבניות (בלי מודל): כל שינוי במצב חייב להיות אחת הפעולות המותרות.

הדוח (נענו / נשאלו שוב) מודפס ונכתב ל-tests/phrasings/REPORT.txt:  pytest tests/test_phrasings.py -s
"""
from __future__ import annotations

from collections import defaultdict

from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.rules.public import RulesStore

from .conftest import CONFIG
from .phrasing_harness import PHRASINGS, Tally, change, describe, judge, load_cases, project, report


def run_case(svc: ConversationService, case) -> dict:
    turn, token = svc.start()
    for text in case.setup:
        turn = svc.send(turn.conversation_id, token, text)
    before = project(svc.view(turn.conversation_id, token).state)
    turn = svc.send(turn.conversation_id, token, case.text)
    return change(before, project(svc.view(turn.conversation_id, token).state))


def test_phrasings_in_template_mode(sessions):
    svc = ConversationService(sessions, RulesStore(CONFIG), LLMChain())     # אין מודל → הקורא בלבד
    cases = load_cases()
    tallies: dict[str, Tally] = defaultdict(Tally)
    for case in cases:
        diff = run_case(svc, case)
        verdict = judge(case, diff)
        t = tallies[case.file]
        if verdict == "answered":
            t.answered += 1
        elif verdict == "asked":
            t.asked += 1
        else:
            t.wrong.append(describe(case, diff))
    text = report("Phrasing set — template mode (no LLM)", tallies, wrong_label=True)
    (PHRASINGS / "REPORT.txt").write_text(text, encoding="utf-8")
    print("\n" + text)
    counts = {name: t.total for name, t in tallies.items()}
    assert len(counts) == 15 and min(counts.values()) >= 15, counts
    wrong = [w for t in tallies.values() for w in t.wrong]
    assert not wrong, "\n".join(wrong)
