"""Verified comparison facts (stage 2 of the reply contract, 16/11).

A "why" about results on screen compares two of them with facts taken from the engine's results only: the two nets and
their difference (code's line, said as is), then each component whose effect on the net differs (what the model may
explain in its words, checked against the same facts). Nothing new is calculated: the component differences add up to
the difference of the displayed nets, exactly as on the cards.
"""
from __future__ import annotations

import re
from decimal import Decimal

from app.modules.conversation.comparison import pair_facts, pick_pair
from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

PROFILE = "קבוצה ב, שכר בסיס 20000, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, אין ילדים"
RANKED = [PROFILE, "יש לי קורולה קרוס, משלמת 300", "הכי משתלם"]       # MG4, BYD DOLPHIN, KONA EV


def services(sessions):
    fake = FakeLLM()
    return (ConversationService(sessions, RulesStore(CONFIG), LLMChain()),
            ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake])), fake)


def ranked(plain):
    turn, token = plain.start()
    for m in RANKED:
        turn = plain.send(turn.conversation_id, token, m)
    return turn.conversation_id, token


def net(result) -> int:
    return int(Decimal(str(result.result["alternative"]["net_display"])))


def amounts(line: str) -> list[int]:
    return [int(x.replace(",", "")) for x in re.findall(r"(\d[\d,]*) ₪", line)]


# ---------- the facts themselves: from the engine, and they add up ----------

def test_the_component_differences_add_up_to_the_difference_of_the_nets(sessions):
    plain, _, _ = services(sessions)
    cid, token = ranked(plain)
    results = plain.view(cid, token).state.results
    for i in range(len(results)):
        for j in range(len(results)):
            if i == j:
                continue
            a, b = results[i], results[j]
            facts = pair_facts(a, b)
            gap = net(a) - net(b)
            assert f"{abs(gap):,} ₪" in facts.net or gap == 0
            signed = 0
            for line in facts.reasons:
                if "גבוה ב-" not in line:
                    continue                                   # "הסיבה העיקרית להפרש: …" names, it adds nothing
                amount = amounts(line)[-1]                     # "…גבוה ב-X ₪."
                signed += amount if f"הנטו ב-{a.label}" in line else -amount
            assert signed == gap, (a.label, b.label, facts.lines())


def test_the_pair_is_the_one_asked_about(sessions):
    plain, _, _ = services(sessions)
    cid, token = ranked(plain)
    state = plain.view(cid, token).state
    first, second, third = state.results
    assert [r.option_id for r in pick_pair(state, [first.option_id])] == [first.option_id, second.option_id]
    assert [r.option_id for r in pick_pair(state, [third.option_id])] == [first.option_id, third.option_id]
    assert [r.option_id for r in pick_pair(state, [second.option_id, third.option_id])] == [second.option_id,
                                                                                             third.option_id]


# ---------- in the conversation ----------

def test_why_the_first_compares_it_with_the_second_from_the_engine(sessions):
    plain, _, _ = services(sessions)
    cid, token = ranked(plain)
    first, second, _ = plain.view(cid, token).state.results
    turn = plain.send(cid, token, "למה דווקא הראשון?")
    assert f"{net(first):,} ₪" in turn.reply and f"{net(second):,} ₪" in turn.reply
    assert f"{net(first) - net(second):,} ₪ בחודש לטובת {first.label}" in turn.reply
    assert "about_results" in turn.decision["intent"] and not turn.decision["results"]


def test_a_vs_b_named_is_the_engines_comparison_not_the_catalogs(sessions):
    plain, _, _ = services(sessions)
    cid, token = ranked(plain)
    _, dolphin, kona = plain.view(cid, token).state.results
    turn = plain.send(cid, token, "מה ההבדל בין הדולפין לקונה?")
    assert "לפי הקטלוג" not in turn.reply and "catalog" not in turn.decision["intent"]
    assert f"{net(dolphin) - net(kona):,} ₪ בחודש לטובת {dolphin.label}" in turn.reply
    assert "איזה רכבים להשוות?" not in turn.reply                     # no unrelated follow-up question


def test_a_car_not_in_the_results_is_still_a_catalog_comparison(sessions):
    plain, _, _ = services(sessions)
    cid, token = ranked(plain)
    turn = plain.send(cid, token, "מה ההבדל בין MG4 לקורולה?")
    assert "catalog" in turn.decision["intent"] and "about_results" not in turn.decision["intent"]


def test_the_model_explains_from_the_facts_and_code_keeps_the_numbers(sessions):
    plain, svc, fake = services(sessions)
    cid, token = ranked(plain)
    _, dolphin, kona = plain.view(cid, token).state.results
    fake.turn(call("rank_options"),
              reply="ב-BYD DOLPHIN (2025) שווי השימוש נמוך יותר, ולכן המס עליו קטן יותר, וגם ההשתתפות נמוכה יותר. [[state]]\n"
                    "רוצים לבדוק עוד רכב?")
    turn = svc.send(cid, token, "ולמה הדולפין יותר טוב מהקונה?")
    d = turn.decision
    assert d["answer_source"] == "model" and "המס עליו קטן יותר" in turn.reply
    assert f"{net(dolphin) - net(kona):,} ₪ בחודש לטובת {dolphin.label}" in turn.reply   # code's line, always
    assert "רוצים לבדוק עוד רכב?" not in turn.reply and not d["results"]              # nothing re-ranked
    assert svc.view(cid, token).state.results == plain.view(cid, token).state.results
    system = next(m.content for m in fake.calls[0].messages if m.role == "system")
    assert "COMPARISON FACTS" in system and f"{net(dolphin) - net(kona):,} ₪" in system   # the facts reached the model


def test_an_explanation_with_a_number_not_in_the_facts_falls_back_to_the_facts(sessions):
    plain, svc, fake = services(sessions)
    cid, token = ranked(plain)
    fake.turn(reply="הדולפין חוסך לכם 900 ₪ בשנה במס. [[state]]")
    turn = svc.send(cid, token, "ולמה הדולפין יותר טוב מהקונה?")
    assert "900" not in turn.reply and turn.decision["answer_source"] == "template"
    assert "שווי השימוש:" in turn.reply                                   # the verified facts instead


def test_an_unsupported_claim_falls_back_to_the_facts(sessions):
    plain, svc, fake = services(sessions)
    cid, token = ranked(plain)
    fake.turn(reply="הדולפין חסכוני יותר בדלק ולכן עדיף. [[state]]").verdict([1]).verdict([1])
    turn = svc.send(cid, token, "ולמה הדולפין יותר טוב מהקונה?")
    assert "חסכוני יותר בדלק" not in turn.reply and "שווי השימוש:" in turn.reply


# ---------- no comparison where none is asked ----------

def test_explaining_a_term_mid_conversation_is_not_a_request_to_compare(sessions):
    plain, _, _ = services(sessions)
    turn, token = plain.start()
    for m in ("קבוצה ב", "שכר בסיס 20000, אין עמלות ואין בונוס"):
        turn = plain.send(turn.conversation_id, token, m)
    for q in ("מה זה שווי שימוש ואיך הוא משפיע על הנטו שלי?", "מה זה השתתפות ואיך היא משפיעה על הנטו?"):
        turn = plain.send(turn.conversation_id, token, q)
        assert "compare" not in turn.decision["intent"] and "כדי להשוות" not in turn.reply, q
        assert turn.stage == Stage.COLLECT_PROFILE and turn.decision["ask"] in turn.reply


def test_a_net_question_without_a_term_is_still_answered_by_the_calculation(sessions):
    plain, _, _ = services(sessions)
    turn, token = plain.start()
    turn = plain.send(turn.conversation_id, token, "קבוצה ב")
    turn = plain.send(turn.conversation_id, token, "אני מרוויחה יותר?")
    assert "compare" in turn.decision["intent"]                          # unchanged: what the answer needs


def test_why_before_any_result_compares_nothing(sessions):
    plain, _, _ = services(sessions)
    turn, token = plain.start()
    for m in (PROFILE, "יש לי קורולה קרוס, משלמת 300"):
        turn = plain.send(turn.conversation_id, token, m)
    turn = plain.send(turn.conversation_id, token, "למה דווקא הראשון?")
    assert not turn.results and "לטובת" not in turn.reply


# ---------- a "why" naming one car on screen (8870 smoke, 16/11) ----------

SELECTED = [PROFILE, "יש לי קורולה קרוס", "לא ידוע", "אני משלמת 300", "תשווה בין MG4 לבין NIRO"]


def selected(plain):
    turn, token = plain.start()
    for m in SELECTED:
        turn = plain.send(turn.conversation_id, token, m)
    return turn.conversation_id, token


import pytest  # noqa: E402


@pytest.mark.parametrize("question", ["למה ב־MG4 יוצא לי יותר נטו?", "למה ב-MG4 יוצא לי יותר נטו?",
                                      "למה עם MG4 הנטו שלי גבוה יותר?"])
def test_why_about_one_car_on_screen_explains_its_result_and_never_asks_to_choose_cars(sessions, question):
    """Root cause: a question about the net that names one car was read as "calculate the net" (compare), so code asked
    which cars to compare although MG4 and NIRO were on screen. A net question naming a result is about that result."""
    plain, _, _ = services(sessions)
    cid, token = selected(plain)
    results = {r.label.split(" (")[0]: r for r in plain.view(cid, token).state.results}
    turn = plain.send(cid, token, question)
    d = turn.decision
    assert "about_results" in d["intent"] and "compare" not in d["intent"] and not d["results"]
    assert "איזה רכבים להשוות?" not in turn.reply
    mg4, niro = results["MG4"], results["NIRO"]
    assert f"{net(mg4) - net(niro):,} ₪ בחודש לטובת {mg4.label}" in turn.reply      # the engine's facts, as is
    assert plain.view(cid, token).state.stage == Stage.PRESENTED


def test_why_about_one_car_with_the_model_keeps_the_results_and_the_facts(sessions):
    plain, svc, fake = services(sessions)
    cid, token = selected(plain)
    fake.turn(reply="")                                  # the live failure: no tool call, nothing written
    turn = svc.send(cid, token, "למה ב־MG4 יוצא לי יותר נטו?")
    assert "איזה רכבים להשוות?" not in turn.reply and "לטובת MG4" in turn.reply
    assert len(svc.view(cid, token).state.results) == 2


def test_a_net_question_about_a_car_not_on_screen_is_still_a_calculation(sessions):
    plain, _, _ = services(sessions)
    cid, token = selected(plain)
    turn = plain.send(cid, token, "כמה נטו יוצא לי עם קונה?")
    assert "about_results" not in turn.decision["intent"]
