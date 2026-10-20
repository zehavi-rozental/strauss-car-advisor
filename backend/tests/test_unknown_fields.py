""""לא יודע" at Q2: recorded per field as unknown, never a default, never a loop; and why SAY fell back."""
from __future__ import annotations

import shutil

from app.modules.conversation.llm.fake import FakeLLM, FailingLLM
from app.modules.conversation.public import ConversationService, LLMChain, Stage
from app.modules.rules.public import RulesStore

from .conftest import CONFIG, CTA_STARTS

PROFILE_Q1 = "מנהלת מוצר, שכר בסיס 18,500"
CAR = "יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש"


def service(sessions, llm=None, config=CONFIG):
    return ConversationService(sessions, RulesStore(config), llm or LLMChain())


def talk(svc, texts):
    turn, token = svc.start()
    for t in texts:
        turn = svc.send(turn.conversation_id, token, t)
    return turn, token


def profile(svc, turn, token):
    return svc.view(turn.conversation_id, token).state.profile


def test_unknown_at_q2_is_recorded_per_field_and_the_flow_goes_on(sessions):
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE_Q1, "לא יודע"])
    p = profile(svc, turn, token)
    assert set(p.unknown_fields) == {"commissions", "bonus", "pension_percent", "study_fund_percent",
                                     "credit_points", "children"}
    assert (p.commissions, p.pension_percent, p.credit_points, p.children) == (None, None, None, None)  # no default
    assert turn.reply.startswith("רשמתי שזה לא ידוע:") and "בלי ערך משוער" in turn.reply
    assert turn.stage == Stage.COLLECT_CURRENT_CAR and "איזה רכב צמוד" in turn.reply       # not Q2 again


def test_a_named_field_is_the_only_unknown(sessions):
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE_Q1, "לא יודעת כמה נקודות זיכוי יש לי"])
    p = profile(svc, turn, token)
    assert p.unknown_fields == ["credit_points"] and p.credit_points is None
    assert "רשמתי שזה לא ידוע: נקודות זיכוי." in turn.reply


def test_unknown_twice_never_repeats_and_never_calculates(sessions):
    svc = service(sessions)
    turn, token = talk(svc, [PROFILE_Q1, "לא יודע", CAR, "אטו 3"])
    assert turn.results == [] and turn.stage == Stage.COLLECT_PROFILE         # no calculation without them
    assert "בלי הערכים האלה אין חישוב" in turn.reply and "כולם מופיעים בתלוש השכר החודשי." in turn.reply
    replies = [turn.reply]
    for _ in range(2):
        turn = svc.send(turn.conversation_id, token, "לא יודע")
        assert turn.results == [] and turn.reply != replies[-1]                  # never the same text twice
        assert turn.reply.split("\n\n")[-1].startswith(CTA_STARTS) and "לא הצלחתי להבין" not in turn.reply
        replies.append(turn.reply)
    assert profile(svc, turn, token).credit_points is None
    # the values found on the payslip: saved, and the calculation runs
    turn = svc.send(turn.conversation_id, token,
                    "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים")
    assert turn.stage == Stage.PRESENTED and turn.results
    assert profile(svc, turn, token).unknown_fields == []


def test_the_payslip_upload_link_is_offered_only_when_configured(sessions, tmp_path):
    # with both flags off: no upload link at all
    off = tmp_path / "config_off"
    shutil.copytree(CONFIG, off)
    ext_off = off / "extensions.yaml"
    ext_off.write_text(ext_off.read_text(encoding="utf-8").replace("payslip_upload_enabled: true",
                                                                   "payslip_upload_enabled: false"),
                       encoding="utf-8")
    svc = service(sessions, config=off)
    turn, _ = talk(svc, [PROFILE_Q1, "לא יודע", CAR, "אטו 3"])
    assert turn.expects["link"] is None                      # no uploader, no external url: no link
    # an external url points the link at a pilot page (url included)
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    ext = config / "extensions.yaml"
    content = ext.read_text(encoding="utf-8").replace("payslip_upload_enabled: true",
                                                       "payslip_upload_enabled: false")
    ext.write_text(content.replace('payslip_upload_url: ""',
                                   'payslip_upload_url: "https://hr.example/upload"'),
                   encoding="utf-8")
    svc = service(sessions, config=config)
    turn, _ = talk(svc, [PROFILE_Q1, "לא יודע", CAR, "אטו 3"])
    assert turn.expects["link"] == {"kind": "payslip_upload", "label": "להעלות תלוש",
                                    "url": "https://hr.example/upload"}
    assert turn.reply.endswith("או העלו את התלוש.")


def test_the_builtin_upload_link_is_offered_without_a_url(sessions, tmp_path):
    """payslip_upload_enabled: true turns on the link for the in-chat uploader — no external URL."""
    config = tmp_path / "config"
    shutil.copytree(CONFIG, config)
    ext = config / "extensions.yaml"
    ext.write_text(ext.read_text(encoding="utf-8").replace("payslip_upload_enabled: false",
                                                           "payslip_upload_enabled: true"),
                   encoding="utf-8")
    svc = service(sessions, config=config)
    turn, _ = talk(svc, [PROFILE_Q1, "לא יודע", CAR, "אטו 3"])
    # the link is offered, but with no url: the client uses the built-in in-chat uploader
    assert turn.expects["link"] == {"kind": "payslip_upload", "label": "להעלות תלוש"}
    assert turn.reply.endswith("או העלו את התלוש.")


# ---------- why SAY fell back to the template ----------

def test_say_reasons(sessions):
    svc = service(sessions, LLMChain([FakeLLM().turn(reply='כתבו כאן, למשל: "קבוצה ג, שכר בסיס 18,500".')]))
    turn, token = svc.start()
    assert svc.send(turn.conversation_id, token, "מה?").say == "guard:unanswered"     # only the CTA line
    svc = service(sessions, LLMChain([FailingLLM()]))
    turn, token = svc.start()
    assert svc.send(turn.conversation_id, token, "מה?").say == "outage"
    svc = service(sessions)
    turn, token = svc.start()
    assert svc.send(turn.conversation_id, token, "מה?").say == "not_tried"


def test_the_example_keeps_thousands_together(sessions):
    """From the live replay: "עמלות 1,000" was cut to "עמלות 1" at the thousands comma."""
    svc = service(sessions)
    turn, _ = talk(svc, [PROFILE_Q1, "לא יודע", CAR, "אטו 3"])
    assert 'למשל: "עמלות 1,000"' in turn.reply
