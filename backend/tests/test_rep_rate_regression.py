"""Regression for QA 22/10 ("יותר מדי בקשות" after ~7 actions in the rep portal), with the real clock and no
new imports, so it runs on the code before the fix as well: there it fails, after the fix it passes.

The mechanism: one limit of 60 a minute per address was shared by the screen's own polling and the rep's
answers; once polling had used it up, the rep's next action was refused."""
from __future__ import annotations

from .conftest import make_client

TOKEN = "rate-limit-regression-token"
AUTH = {"X-Rep-Token": TOKEN}
MISSING = "/api/rep/questions/00000000-0000-0000-0000-000000000000/answer"


def test_the_screens_polling_never_blocks_the_reps_answers(engine):
    client = make_client(engine, rep_access_token=TOKEN)
    for _ in range(60):          # the old screen polled 40 a minute; a minute and a half of it, or a few open tabs
        client.get("/api/rep/questions?status=OPEN", headers=AUTH)
    answers = [client.post(MISSING, json={"value": "300"}, headers=AUTH).status_code for _ in range(10)]
    assert 429 not in answers    # admitted (404: there is no such question in this test)
