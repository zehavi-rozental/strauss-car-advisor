"""The spec eval set against the real LLM chain. Paid calls: runs only on request.

    pytest -m eval tests/eval/test_spec_eval_live.py           # all 57 cases, ~1-3 LLM calls each
    EVAL_ONLY=sal_free_1,role_2 pytest -m eval tests/eval/...  # a subset

Before anything runs, the estimated number of calls is printed; above EVAL_MAX_CALLS (default 200) it stops.
The report (spec-eval-live.md in backend/, git-ignored) shows the spec targets: extraction >= 95% per field,
the right tool >= 95%, and 0 replies with an invented amount.
"""
from __future__ import annotations

import logging
import os
import time

import pytest

from app.modules.conversation.public import build_llm_chain
from app.shared.settings import Settings

from ..conftest import BACKEND
from .spec_eval import CASES, Summary, run_case

pytestmark = pytest.mark.eval

MAX_CALLS = int(os.environ.get("EVAL_MAX_CALLS", "200"))
PAUSE_S = float(os.environ.get("EVAL_TURN_PAUSE_S", "2"))
CALLS_PER_TURN = 3         # tool round + reply round + the claim verifier (worst case without a retry)


def test_spec_eval_live():
    chain = build_llm_chain(Settings())
    if not chain.available:
        pytest.skip("no LLM key (LLM_CHAIN / GEMINI_API_KEY)")
    only = {x for x in os.environ.get("EVAL_ONLY", "").split(",") if x}
    cases = [c for c in CASES if not only or c["id"] in only]
    estimate = len(cases) * CALLS_PER_TURN
    print(f"\nestimated LLM calls: <= {estimate}")
    if estimate > MAX_CALLS:
        pytest.fail(f"estimated {estimate} LLM calls > EVAL_MAX_CALLS={MAX_CALLS}: ask before running")
    logging.disable(logging.CRITICAL)
    results = []
    for case in cases:
        results.append(run_case(case, "live", chain))
        time.sleep(PAUSE_S)
    logging.disable(logging.NOTSET)
    summary = Summary(f"live ({chain.mode})", results)
    (BACKEND / "spec-eval-live.md").write_text(summary.markdown("Spec eval — live LLM"), encoding="utf-8")
    not_llm = [r.id for r in results if r.path not in ("llm", "parser")]
    print(summary.markdown("Spec eval — live LLM"))
    if not_llm:
        print(f"answered without the LLM (cooling down / fallback): {not_llm}")

    f_ok, f_all = summary.fields
    a_ok, a_all = summary.actions
    assert not any(r.stray_numbers for r in results), "an invented amount reached the employee"
    assert f_ok / f_all >= 0.95, f"extraction {f_ok}/{f_all}"
    assert a_ok / a_all >= 0.95, f"tool choice {a_ok}/{a_all}"
