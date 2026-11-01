"""Free and unexpected wording at the question about today's car, against the real LLM chain (1/11). Paid calls:
runs only on request (`pytest -m eval tests/eval/test_free_text_live.py`, ~2-4 calls per message, 4 messages).

The hybrid routing: word lists decide only without a model; free text goes to the model, which understands it and
calls show_cars when the employee wants to see the cars. Checked for correctness, not only a 200: nothing is assumed
(no "no car", no copay, no "no car or copay 0?" question), the car question stays open, and the reply either shows the
real catalog (every line a catalog car) or asks about today's car.
"""
from __future__ import annotations

import pytest

from app.modules.conversation.public import build_llm_chain
from app.shared.settings import Settings

from ..conftest import make_client

pytestmark = pytest.mark.eval

PROFILE = ["קבוצה ב", "שכר בסיס 18000",
           "אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"]
MESSAGES = [
    "אני לא זוכרת, תציג לי אפשרויות ואזהה",
    "אין לי מושג מה הדגם, יש איזה רשימה לבחור ממנה?",
    "שכחתי איך קוראים לאוטו, תעזור לי למצוא",
    "רגע אני לא בטוחה, זה משהו של טויוטה נראה לי",
]


@pytest.mark.parametrize("text", MESSAGES)
def test_free_text_at_todays_car_with_the_real_model(engine, text):
    chain = build_llm_chain(Settings())
    if not chain.available:
        pytest.skip("no LLM key (LLM_CHAIN / GEMINI_API_KEY)")
    c = make_client(engine, llm=chain)
    cid = c.post("/api/conversations").json()["conversation_id"]
    plain = make_client(engine)
    plain.cookies = c.cookies
    for m in PROFILE:                                                   # the setup without paid calls
        plain.post(f"/api/conversations/{cid}/messages", json={"text": m})
    turn = c.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()
    reply = turn["reply"]
    print(f"\n>>> {text}\n{reply}")
    view = c.get(f"/api/conversations/{cid}").json()
    assert view["today_car"] is None                                    # nothing recorded for today's car
    assert not [f for f in turn["expects"]["fields"] if f["field"] == "ambiguity"]
    assert "car" in [f["field"] for f in turn["expects"]["fields"]]      # the question stays open
    labels = {car["label"] for car in c.get("/api/catalog").json()}
    listed = [ln[2:] for ln in reply.splitlines() if ln.startswith("- ")]
    assert set(listed) <= labels                                        # a list is only real catalog cars
    assert listed or "רכב" in reply                                     # the list, or a question about the car
    assert "נציג" not in reply                                          # a list request is never a question for the rep
