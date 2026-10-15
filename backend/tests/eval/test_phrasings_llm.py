"""סט הניסוחים מול מודל השפה האמיתי (LLM_MODE=llm_first). רץ רק במפורש:  pytest -m eval -k phrasings

ההכנה של כל מקרה (ההודעות שמביאות לשאלה) רצה בקורא בלבד, על אותו מסד — רק הודעת המקרה עצמה מגיעה למודל.
  EVAL_TURN_PAUSE_S   השהיה אחרי כל הודעה למודל (מכסה)
  EVAL_PHRASINGS      תת-קבוצה: שמות קבצים ו/או N (N המקרים הראשונים בכל קובץ), למשל "profile,options,3"
הדוח (נענו / נשאלו שוב / שגויים) נכתב ל-tests/phrasings/REPORT_LLM.txt. פעולה שגויה מכשילה את ההערכה.
"""
from __future__ import annotations

import os
import time
from collections import defaultdict

import pytest

from app.modules.conversation.public import ConversationService, LLMChain, build_llm_chain
from app.modules.rules.public import RulesStore
from app.shared.settings import Settings

from ..conftest import CONFIG
from ..phrasing_harness import PHRASINGS, Tally, change, describe, judge, load_cases, project, report

pytestmark = pytest.mark.eval

TURN_PAUSE_S = float(os.environ.get("EVAL_TURN_PAUSE_S", "30"))
CHAIN = build_llm_chain(Settings())


def _subset() -> tuple[set[str], int | None]:
    names: set[str] = set()
    first = None
    for part in os.environ.get("EVAL_PHRASINGS", "").split(","):
        part = part.strip()
        if part.isdigit():
            first = int(part)
        elif part:
            names.add(part.removesuffix(".yaml"))
    return names, first


@pytest.mark.skipif(not CHAIN.available, reason="אין רשומה זמינה בשרשרת (LLM_CHAIN / מפתחות)")
def test_phrasings_against_the_real_llm(sessions):
    rules = RulesStore(CONFIG)
    setup_svc = ConversationService(sessions, rules, LLMChain(), llm_mode="parser_first")   # הכנה — בלי מודל
    llm_svc = ConversationService(sessions, rules, CHAIN, llm_mode="llm_first")
    names, first = _subset()
    cases = load_cases(names or None, first)
    assert cases, f"no cases for EVAL_PHRASINGS={os.environ.get('EVAL_PHRASINGS')!r}"

    tallies: dict[str, Tally] = defaultdict(Tally)
    not_llm: list[str] = []                  # המודל לא היה זמין והקורא ענה במקומו
    for case in cases:
        turn, token = setup_svc.start()
        for text in case.setup:
            turn = setup_svc.send(turn.conversation_id, token, text)
        before = project(setup_svc.view(turn.conversation_id, token).state)
        turn = llm_svc.send(turn.conversation_id, token, case.text)
        diff = change(before, project(llm_svc.view(turn.conversation_id, token).state))
        if turn.path not in ("llm", "parser"):
            not_llm.append(f"{case.file}#{case.index} ({turn.path})")
        verdict = judge(case, diff)
        t = tallies[case.file]
        if verdict == "answered":
            t.answered += 1
        elif verdict == "asked":
            t.asked += 1
        else:
            t.wrong.append(describe(case, diff))
        time.sleep(TURN_PAUSE_S)

    text = report(f"Phrasing set — LLM_MODE=llm_first, chain: {CHAIN.mode}", tallies, wrong_label=True)
    if not_llm:
        text += f"\nanswered without the LLM (fallback / cooling down): {len(not_llm)}\n  " + ", ".join(not_llm) + "\n"
    (PHRASINGS / "REPORT_LLM.txt").write_text(text, encoding="utf-8")
    print("\n" + text)
    wrong = [w for t in tallies.values() for w in t.wrong]
    assert not wrong, "\n".join(wrong)
