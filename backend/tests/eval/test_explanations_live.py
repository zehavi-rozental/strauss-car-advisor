"""Explanations with the real LLM chain (10/11). Paid calls: runs only on request
(`pytest -m eval tests/eval/test_explanations_live.py`; at most EXPLAIN_MAX_CALLS Google calls, refused past it).

The live bug on 8870: "מה זה אומר הפרשות?" to the contributions question got "why we need the data" and the question
again (Gemini's explanation had no grounded source and the verifier rejected it). Checked for correctness: the term is
explained, nothing is saved or assumed, a follow-up keeps the subject, the values typed next are saved, and catalog
facts and unverified properties keep their sources.
"""
from __future__ import annotations

import logging
import os

import httpx
import pytest

from app.modules.conversation.public import build_llm_chain
from app.shared.settings import Settings

from ..conftest import make_client

pytestmark = pytest.mark.eval

SETUP = ["קבוצה ב", "שכר בסיס 18000", "אין עמלות ואין בונוס, 2.75 נקודות זיכוי, 2 ילדים", "קורולה 2026, משלמת 300",
         "MG4"]
WHY_ASK = "הנתונים נחוצים כדי לחשב"
MAX_CALLS = int(os.environ.get("EXPLAIN_MAX_CALLS", "45"))


@pytest.fixture
def capped(monkeypatch):
    """Every request to Google is counted before it leaves; past the cap it is refused (the turn falls back)."""
    sent = {"n": 0}
    real = httpx.Client.send

    def send(self, request, *a, **kw):
        if "googleapis.com" in str(request.url):
            sent["n"] += 1
            if sent["n"] > MAX_CALLS:
                raise httpx.ConnectError("call cap reached", request=request)
        return real(self, request, *a, **kw)
    monkeypatch.setattr(httpx.Client, "send", send)
    return sent


def conversation(engine):
    chain = build_llm_chain(Settings())
    if not chain.available:
        pytest.skip("no LLM key (LLM_CHAIN / GEMINI_API_KEY)")
    c = make_client(engine, llm=chain)
    cid = c.post("/api/conversations").json()["conversation_id"]
    plain = make_client(engine)
    plain.cookies = c.cookies
    for m in SETUP:                                                     # the setup without paid calls
        last = plain.post(f"/api/conversations/{cid}/messages", json={"text": m}).json()
    assert "הפרשות" in last["reply"]                                    # the contributions question is on screen

    def say(text):
        turn = c.post(f"/api/conversations/{cid}/messages", json={"text": text}).json()
        print(f"\n>>> {text}\n{turn['reply']}")
        return turn
    return c, cid, say


def profile(c, cid):
    return c.get(f"/api/conversations/{cid}").json()["profile"]


def test_contributions_explained_then_the_values_said_next_are_saved(engine, capped, caplog):
    caplog.set_level(logging.INFO, logger="app.modules.conversation.public")
    c, cid, say = conversation(engine)
    first = say("מה זה אומר הפרשות?")["reply"]
    assert WHY_ASK not in first and "פנסיה" in first and "השתלמות" in first
    assert (profile(c, cid)["pension_percent"], profile(c, cid)["study_fund_percent"]) == (None, None)
    where = say("איפה זה בתלוש?")["reply"]
    assert "תלוש" in where and ("ניכוי" in where or "פנסיה" in where)
    again = say("לא הבנתי")["reply"]
    assert "פנסיה" in again
    done = say("אוקיי, אז 6% לפנסיה ו-2.5% לקרן השתלמות")
    p = profile(c, cid)
    assert (p["pension_percent"], p["study_fund_percent"]) == ("6", "2.5")
    assert "1,029" in done["reply"]                                     # the engine's figure, unchanged
    print("\n" + "\n".join(r.getMessage() for r in caplog.records if "say=" in r.getMessage()))
    print(f"google calls so far: {capped['n']}")


def test_gross_net_catalog_and_an_unverified_property(engine, capped, caplog):
    caplog.set_level(logging.INFO, logger="app.modules.conversation.public")
    c, cid, say = conversation(engine)
    gross = say("מה ההבדל בין ברוטו לנטו?")["reply"]
    assert "ברוטו" in gross and "נטו" in gross and "כדי להשוות, חסרים" not in gross
    copay = say("כמה ההשתתפות של ה-MG4?")["reply"]
    assert "250" in copay                                               # the catalog amount, nothing invented
    roof = say("יש ל-MG4 גג נפתח?")["reply"]
    assert "נציג" in roof                                               # not in any verified source: offered to the rep
    assert (profile(c, cid)["pension_percent"], profile(c, cid)["study_fund_percent"]) == (None, None)
    print("\n" + "\n".join(r.getMessage() for r in caplog.records if "say=" in r.getMessage()))
    print(f"google calls so far: {capped['n']}")
