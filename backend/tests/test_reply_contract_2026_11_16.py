"""Structured reply contract (stage 1 of the 16/11 RCA): one turn decision, composed once.

RCA findings these behaviours pin (live traces, 16/11):
- S1: an explanation mid-collection; the model added its own question / acknowledgement next to code's.
- S2: a question about the calculation with today's copay unknown; a format guard ("cta") threw away the model's
  answer and the template that replaced it did not answer.
- S3: a partial salary answer; the model asked three fields while code's policy asks one item.
- S4: "why" after a ranking; an unrelated "choose cars" question was added, the numbers were lost.
- S5: a changed value; one "עדכנתי", recalculated, the text and the results agree.
- S6: cars chosen before the data is complete; the selection and "the comparison waits" were dropped.

The decision (TurnResult.decision) is checked, and the state — not only the wording:
- the next question is code's (decision["ask"]), and the reply has exactly that one question;
- acknowledgements come from the state diff only (decision["acks"]), never from the model;
- the model's explanation is used when it passes the facts guards (sources, numbers, claim verifier), else the
  verified template explanation (decision["answer_source"]).
"""
from __future__ import annotations

from app.modules.conversation.llm.fake import FakeLLM, call
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG

PROFILE = "קבוצה ב, שכר בסיס 20000, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, אין ילדים"
ACK_STARTS = ("רשמתי", "עדכנתי", "סימנתי", "זיהיתי")


def services(sessions):
    fake = FakeLLM()
    return (ConversationService(sessions, RulesStore(CONFIG), LLMChain()),
            ConversationService(sessions, RulesStore(CONFIG), LLMChain([fake])), fake)


def setup(plain, messages):
    turn, token = plain.start()
    for m in messages:
        turn = plain.send(turn.conversation_id, token, m)
    return turn.conversation_id, token


def questions(reply: str) -> list[str]:
    return [ln for ln in reply.splitlines() if "?" in ln]


def only_code_asks(turn) -> bool:
    """Every question / call to action in the reply is the decision's own (its ask or its call to action)."""
    d = turn.decision
    own = " | ".join([d["ask"], d["cta"]])
    return all(ln.strip() in own for ln in questions(turn.reply))


def model_lines(reply: str, model_reply: str) -> list[str]:
    """Lines of the scripted model reply that reached the employee."""
    return [ln for ln in model_reply.splitlines() if ln.strip() and ln.split("[[")[0].strip() in reply]


# ---------- S1: an explanation in the middle of the salary questions ----------

S1_MODEL = ("קרן השתלמות היא חיסכון נפרד מהפנסיה, שמפרישים אליו מהשכר בכל חודש. [[faq:study_fund_percent]]\n"
            "רשמתי: שכר בסיס 20,000 ₪.\n"
            "מה אחוז ההפרשה שלכם לקרן השתלמות?")


def test_s1_explanation_is_the_models_the_question_and_acks_are_codes(sessions):
    plain, svc, fake = services(sessions)
    cid, token = setup(plain, ["קבוצה ב", "שכר בסיס 20000, אין עמלות ואין בונוס"])
    fake.turn(reply=S1_MODEL)
    turn = svc.send(cid, token, "מה זה קרן השתלמות?")
    d = turn.decision
    assert d["answer_source"] == "model" and "קרן השתלמות היא חיסכון" in turn.reply
    assert d["changes"] == {} and d["acks"] == []                       # nothing saved: nothing acknowledged
    assert not any(ln.startswith(ACK_STARTS) for ln in turn.reply.splitlines())
    assert questions(turn.reply) == [d["ask"]] and "פנסיה" in d["ask"]   # one question, code's, the waiting one
    p = svc.view(cid, token).state.profile
    assert p.study_fund_percent is None and p.pension_percent is None


def test_s1_a_rejected_explanation_falls_back_to_the_verified_one(sessions):
    plain, svc, fake = services(sessions)
    cid, token = setup(plain, ["קבוצה ב", "שכר בסיס 20000, אין עמלות ואין בונוס"])
    fake.turn(reply="קרן השתלמות היא חשבון בנק של המעסיק. [[faq:study_fund_percent]]\nמה האחוז?")
    fake.verdict([1]).verdict([1])                                       # unsupported, the retry fails too
    turn = svc.send(cid, token, "מה זה קרן השתלמות?")
    d = turn.decision
    assert d["answer_source"] == "template" and "חשבון בנק" not in turn.reply
    assert "קרן השתלמות היא חיסכון" in turn.reply                       # the verified FAQ explanation
    assert questions(turn.reply) == [d["ask"]]


def test_s1_without_a_model_the_template_explains_and_asks_the_same(sessions):
    plain, _, _ = services(sessions)
    cid, token = setup(plain, ["קבוצה ב", "שכר בסיס 20000, אין עמלות ואין בונוס"])
    turn = plain.send(cid, token, "מה זה קרן השתלמות?")
    assert "קרן השתלמות היא חיסכון" in turn.reply and questions(turn.reply) == [turn.decision["ask"]]


# ---------- S2: today's copay unknown, and a question about the calculation ----------

def test_s2_a_question_mid_flow_is_answered_and_the_flow_question_is_codes(sessions):
    plain, svc, fake = services(sessions)
    cid, token = setup(plain, [PROFILE, "יש לי קורולה קרוס", "לא ידוע", "לא ידוע"])   # the second is recorded
    assert svc.view(cid, token).state.current.copay_unknown                # today's copay: recorded as unknown
    model = ("ההשתתפות היא הסכום שיורד לכם מהנטו בכל חודש עבור הרכב. [[faq:copay]]\n"
             "בדקתי את הנתונים שלכם.\n"
             "איזה רכבים להשוות?")
    fake.turn(reply=model)
    turn = svc.send(cid, token, "איך אתה מחשב את הנטו בלי ההשתתפות?")
    d = turn.decision
    assert d["answer_source"] == "model" and "ההשתתפות היא הסכום שיורד לכם מהנטו" in turn.reply
    assert only_code_asks(turn) and "איזה רכבים להשוות?" not in turn.reply   # never the model's own question
    assert not turn.results and turn.stage != Stage.PRESENTED            # no net without today's copay


# ---------- S3: a partial salary answer ----------

def test_s3_a_partial_answer_is_acknowledged_once_and_one_item_is_asked(sessions):
    plain, svc, fake = services(sessions)
    cid, token = setup(plain, ["קבוצה ב", "שכר בסיס 20000"])
    fake.turn(call("save_profile", pension_percent=6, children_under_23=0),
              reply="רשמתי: פנסיה 6%, ילדים 0.\nהאם יש לכם עמלות או בונוסים, ומה אחוז ההפרשה לקרן השתלמות?")
    turn = svc.send(cid, token, "פנסיה 6% ואין לי ילדים")
    d = turn.decision
    assert d["changes"] == {"pension_percent": "6", "children": "0"}
    assert len(d["acks"]) == 1 and sum(ln.startswith(ACK_STARTS) for ln in turn.reply.splitlines()) == 1
    assert questions(turn.reply) == [d["ask"]] and "עמלות" in d["ask"] and "השתלמות" not in d["ask"]
    assert "commissions" in d["missing_fields"] and "study_fund_percent" in d["missing_fields"]


# ---------- S4: "why" after a ranking ----------

def test_s4_why_after_the_ranking_answers_and_adds_no_unrelated_question(sessions):
    plain, svc, fake = services(sessions)
    cid, token = setup(plain, [PROFILE, "יש לי קורולה קרוס, משלמת 300", "הכי משתלם"])
    ranked = svc.view(cid, token).state.results
    # live 16/11 after the contract: the model ranked again on a "why" question; refused, nothing re-ranked
    fake.turn(call("rank_options"), reply="ב-MG4 שווי השימוש נמוך יותר, ולכן המס קטן. [[state]]\nאיזה רכבים להשוות?")
    turn = svc.send(cid, token, "למה דווקא הראשון?")
    d = turn.decision
    first = ranked[0].result
    # stage 2: the engine's two nets and their difference are code's line; the model explains why from the facts
    assert "about_results" in d["intent"] and d["answer_source"] == "model"
    assert f"{int(first['alternative']['net_display']):,}" in turn.reply and "שווי השימוש נמוך יותר" in turn.reply
    assert "איזה רכבים להשוות?" not in turn.reply
    assert d["ask"] == "" and questions(turn.reply) == ([d["cta"]] if "?" in d["cta"] else [])
    state = svc.view(cid, token).state
    assert state.results == ranked and not d["results"]                 # nothing recalculated, nothing changed


def test_s4_without_a_model_the_reason_carries_the_engines_numbers(sessions):
    plain, _, _ = services(sessions)
    cid, token = setup(plain, [PROFILE, "יש לי קורולה קרוס, משלמת 300", "הכי משתלם"])
    first = plain.view(cid, token).state.results[0]
    turn = plain.send(cid, token, "למה דווקא הראשון?")
    assert f"{int(first.result['alternative']['net_display']):,}" in turn.reply


# ---------- S5: a changed value, recalculated ----------

def test_s5_a_change_is_acknowledged_once_recalculated_and_text_matches_results(sessions):
    plain, svc, fake = services(sessions)
    cid, token = setup(plain, [PROFILE, "יש לי קורולה קרוס, משלמת 300", "MG4"])
    fake.turn(call("save_profile", base_salary=22000), reply="רשמתי: שכר בסיס 22,000 ₪.\nרוצים לראות עוד רכבים?")
    turn = svc.send(cid, token, "בעצם השכר שלי 22000")
    d = turn.decision
    assert d["changes"] == {"base": "22000"} and d["results"] and turn.results
    assert turn.reply.count("עדכנתי") == 1 and "רשמתי: שכר בסיס 22,000" not in turn.reply
    r = turn.results[0].result
    assert f"{abs(int(r['delta_display'])):,}" in turn.reply                # the text says the engine's number
    assert not svc.view(cid, token).state.results == [] and not turn.results_stale


# ---------- S6: cars chosen before the data is complete ----------

def test_s6_cars_chosen_early_are_confirmed_and_wait_for_the_data(sessions):
    plain, svc, fake = services(sessions)
    cid, token = setup(plain, ["קבוצה ב", "שכר בסיס 20000"])
    # live 16/11: the model also ranked next to its selection; named cars without a ranking word are not a ranking
    fake.turn(call("select_options", options=["MG4", "NIRO"]), call("rank_options"),
              reply="רשמתי: השוואה בין MG4 לבין נירו.\nאילו רכיבי שכר נוספים יש לכם?")
    turn = svc.send(cid, token, "אני רוצה להשוות את MG4 ונירו")
    d = turn.decision
    state = svc.view(cid, token).state
    assert state.selected == ["CAR-06", "CAR-05"] and not turn.results and not d["results"]
    assert any(a.startswith("בחרתם להשוות") for a in d["acks"]) and "בחרתם להשוות" in turn.reply
    assert d["missing_fields"] and only_code_asks(turn) and "אילו רכיבי שכר נוספים יש לכם?" not in turn.reply
    assert "רשמתי: השוואה" not in turn.reply                             # the model's own acknowledgement
    assert not state.rank_requested and "הכי משתלמות" not in turn.reply    # never turned into a ranking
    assert turn.reply.count("חסר") <= 2                                    # "missing" said once, plus the progress line


# ---------- a data-only message needs no explanation: no extra model call ----------

def test_a_message_code_read_in_full_needs_no_model_reply(sessions):
    plain, svc, fake = services(sessions)
    cid, token = setup(plain, ["קבוצה ב"])
    turn = svc.send(cid, token, "שכר בסיס 20000")
    assert turn.decision["changes"] == {"base": "20000"} and fake.calls == []
