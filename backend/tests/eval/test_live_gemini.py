"""הערכה חיה: שיחת נועה לוי מול הספק האמיתי (Gemini או מה שמוגדר ב-.env).

רץ רק כשמוגדר LLM_API_KEY, ורק במפורש:  pytest -m eval
"""
from __future__ import annotations

import os
import time

import pytest

from app.modules.conversation.public import build_llm_chain
from app.shared.settings import Settings

from ..conftest import BACKEND, make_client

pytestmark = pytest.mark.eval

# מכסה חינמית של Gemini: ~10 בקשות לדקה, ותור שיחה = 2–3 בקשות. השהיה בין תורות שומרת מתחת למכסה.
TURN_PAUSE_S = float(os.environ.get("EVAL_TURN_PAUSE_S", "30"))

SETTINGS = Settings(_env_file=BACKEND / ".env")
NOA_PROFILE = ("אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, "
               "2.75 נקודות זיכוי, 2 ילדים")


@pytest.mark.skipif(not SETTINGS.llm_api_key, reason="LLM_API_KEY לא מוגדר")
def test_noa_levi_against_real_provider(engine):
    chain = build_llm_chain(SETTINGS)
    if not chain.available:
        pytest.skip("אין ספק מודל שפה מוגדר (LLM_PROVIDER / LLM_MODEL)")
    client = make_client(engine, llm=chain)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(text: str) -> dict:
        r = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        body = r.json()
        time.sleep(TURN_PAUSE_S)
        return body

    body = say(NOA_PROFILE)
    assert body["stage"] == "COLLECT_CURRENT_CAR", body["reply"]

    body = say("יש לי קורולה קרוס, ואני משתתפת היום ב-300 ש\"ח בחודש")
    assert body["stage"] == "CHOOSE_OPTIONS", body["reply"]

    body = say("אני רוצה לבדוק את BYD אטו 3 החשמלי")
    assert body["stage"] == "PRESENTED", body["reply"]
    # רק התוצאות מנוסחות במודל; תבנית כאן = הספק נכשל או ששומר המספרים חסם (ראו הלוג)
    assert body["source"] == "llm", f"התור נפל לתבנית: {body['reply']}"
    [result] = body["results"]
    assert result["label"] == "ATTO 3 (2025)"
    assert result["delta_display"] == "661"
    assert "661" in body["reply"]
