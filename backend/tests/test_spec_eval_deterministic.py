"""The spec eval set (tests/eval/spec_cases.yaml) without a live LLM, on every run.

Two modes, reported separately (spec-eval-template.md / spec-eval-outage.md in backend/, git-ignored):
- template: no LLM key at all;
- outage:   the LLM fails on every call (Gemini down during the demo).
Without a model the parser reads less and asks again; that is allowed. What is never allowed: a wrong or guessed
value, an invented amount, a dead end or a crash. Extraction must not drop below the measured baseline.
The live-LLM run of the same set is tests/eval/test_spec_eval_live.py (pytest -m eval, on request).
"""
from __future__ import annotations

import logging

import pytest

from .conftest import BACKEND
from .eval.spec_eval import CASES, Summary, run_case

# measured on 21/10 (deterministic: the same numbers on every run); raise them when the parser improves
BASELINE = {"template": {"fields": 37, "actions": 39}, "outage": {"fields": 20, "actions": 10}}


@pytest.fixture(scope="module", autouse=True)
def _quiet():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


@pytest.mark.parametrize("mode", ["template", "outage"])
def test_spec_eval_without_a_live_llm(mode):
    cases = CASES if mode == "template" else [c for c in CASES if c.get("outage")]
    summary = Summary(mode, [run_case(c, mode) for c in cases])
    (BACKEND / f"spec-eval-{mode}.md").write_text(summary.markdown(f"Spec eval — {mode}"), encoding="utf-8")

    assert len(CASES) >= 50
    problems = [(r.id, r.failure_category, r.wrong_saves, r.stray_numbers, r.error) for r in summary.results
                if r.error or r.wrong_saves or r.stray_numbers or r.dead_end]
    assert not problems, problems
    assert summary.fields[0] >= BASELINE[mode]["fields"], summary.fields
    assert summary.actions[0] >= BASELINE[mode]["actions"], summary.actions
