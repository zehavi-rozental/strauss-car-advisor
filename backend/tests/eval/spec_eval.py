"""Runner for the spec eval set (spec_cases.yaml): the spec's three AI targets, measured per case.

- extraction accuracy per field: the values each message must save, saved with the right value;
- tool choice: the turn took the expected path (save / select / rank / rep_offer / answer / hold);
- invented numbers: an amount in the reply that is not in the state, the employee's words, the FAQ or the
  catalog (the same check as the conversation eval);
plus wrong saves (a value saved that the employee did not give, or a field that had to stay empty) and dead ends.

The same runner scores three things that must not be mixed:
- `template`  no LLM at all (the deterministic parser + templates; what the demo does without a key),
- `outage`    an LLM that always fails (the fallback path when Gemini is down),
- `live`      the real LLM chain (tests/eval/test_spec_eval_live.py, run on request only).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import sessionmaker

from app.modules.conversation.guard import collect_numbers
from app.modules.conversation.llm.port import LLMError
from app.modules.conversation.parsing import numbers_in
from app.modules.conversation.public import ConversationService, LLMChain
from app.modules.rules.public import RulesStore

from ..conftest import CONFIG, build_engine
from ..phrasing_harness import change, norm, project

HERE = Path(__file__).parent
DATA = yaml.safe_load((HERE / "spec_cases.yaml").read_text(encoding="utf-8"))
SETUPS: dict[str, list[str]] = DATA["SETUPS"]
CASES: list[dict[str, Any]] = DATA["cases"]

# values the employee gives; a change to one of these that the case did not expect is a wrong save
VALUE_KEYS = {"occupation", "group", "base", "commissions", "bonus", "pension", "study_fund", "credit_points",
              "children", "car", "copay", "maintenance", "use_value", "selected"}


class Down:
    """A provider that is always down: every turn takes the outage path."""
    name = "down"

    def complete(self, *args, **kwargs):
        raise LLMError("down")


@dataclass
class CaseResult:
    id: str
    category: str
    mode: str
    text: str
    reply: str = ""
    path: str = ""
    fields_expected: int = 0
    fields_ok: int = 0
    action: str = ""
    action_ok: bool = True
    wrong_saves: list[str] = field(default_factory=list)
    stray_numbers: list[str] = field(default_factory=list)
    dead_end: bool = False
    error: str = ""

    @property
    def passed(self) -> bool:
        return (not self.error and self.fields_ok == self.fields_expected and self.action_ok
                and not self.wrong_saves and not self.stray_numbers and not self.dead_end)

    @property
    def failure_category(self) -> str:
        if self.error:
            return "crash"
        if self.stray_numbers:
            return "invented_number"
        if self.wrong_saves:
            return "wrong_or_guessed_value"
        if self.fields_ok < self.fields_expected:
            return "value_not_extracted"
        if not self.action_ok:
            return "wrong_tool_or_path"
        if self.dead_end:
            return "dead_end"
        return ""


def _best_expected(case: dict[str, Any], diff: dict[str, Any]) -> dict[str, Any]:
    if "expect" in case:
        return {k: norm(v) for k, v in case["expect"].items()}
    options = [{k: norm(v) for k, v in o.items()} for o in case.get("any_of") or [{}]]
    return max(options, key=lambda o: sum(diff.get(k) == v for k, v in o.items()))


def _action_ok(action: str, diff: dict[str, Any], expected: dict[str, Any], state, turn) -> bool:
    saved_values = VALUE_KEYS & diff.keys()
    if action == "save":
        return all(diff.get(k) == v for k, v in expected.items())
    if action == "select":
        return "selected" in diff and diff["selected"] == expected.get("selected", diff["selected"])
    if action == "rank":
        return state.ranked_count > 0 and bool(state.selected)
    if action == "rep_offer":
        offered = any(f.get("field") == "ask_rep" for f in turn.expects.get("fields") or [])
        return (offered or state.pending_rep_question is not None) and not saved_values
    if action == "hold":
        return "confirm" in diff and not saved_values
    if action == "answer":
        return not saved_values
    raise ValueError(f"unknown action {action!r}")


def run_case(case: dict[str, Any], mode: str, chain=None) -> CaseResult:
    """One case on a fresh database: the setup without an LLM, then the case message in `mode`."""
    result = CaseResult(id=case["id"], category=case["category"], mode=mode, text=case["text"],
                        action=case["action"])
    sessions = sessionmaker(bind=build_engine())
    store = RulesStore(CONFIG)
    setup_svc = ConversationService(sessions, store, LLMChain(), llm_mode="parser_first")
    llm = {"template": LLMChain(), "outage": LLMChain([Down()])}.get(mode, chain)
    svc = ConversationService(sessions, store, llm, llm_mode="llm_first")
    snap = store.current
    try:
        turn, token = setup_svc.start()
        cid = turn.conversation_id
        users = list(SETUPS[case["setup"]])
        for text in users:
            setup_svc.send(cid, token, text)
        before = project(setup_svc.view(cid, token).state)
        users.append(case["text"])
        turn = svc.send(cid, token, case["text"])
        state = svc.view(cid, token).state
    except Exception as exc:          # a crash is a result, not an aborted run
        result.error = f"{type(exc).__name__}: {exc}"[:200]
        return result

    after = project(state)
    diff = change(before, after)
    expected = _best_expected(case, diff)
    result.reply, result.path = turn.reply, turn.path
    result.fields_expected = len(expected)
    result.fields_ok = sum(diff.get(k) == v for k, v in expected.items())
    result.action_ok = _action_ok(case["action"], diff, expected, state, turn)

    for key in case.get("keep") or []:
        if key in diff:
            result.wrong_saves.append(f"{key}: {before.get(key)} -> {diff[key]} (must stay)")
    for key in VALUE_KEYS & diff.keys():
        if key == "selected" and case["action"] == "rank":
            continue                       # ranking selects the best cars: that is the expected outcome
        if key in expected and diff[key] != expected[key]:
            result.wrong_saves.append(f"{key}={diff[key]} (expected {expected[key]})")
        elif key not in expected and key not in (case.get("keep") or []):
            result.wrong_saves.append(f"{key}={diff[key]} (not given in this message)")

    allowed = set().union(collect_numbers(state.model_dump(mode="json")), collect_numbers(users),
                          collect_numbers([e.answer for e in snap.faq.entries]),
                          collect_numbers([c.label for c in snap.catalog]), {Decimal(3), Decimal(12), Decimal(13)})
    facts = re.split(r"למשל", turn.reply)[0]           # examples in a call to action are not facts
    result.stray_numbers = [str(n) for n in numbers_in(facts) if abs(n) not in allowed]

    finished = bool(turn.results) or state.pending_rep is not None
    result.dead_end = not turn.reply.strip() or (
        not finished and (turn.expects.get("type") == "none" or not turn.expects.get("fields")))
    return result


@dataclass
class Summary:
    mode: str
    results: list[CaseResult]

    def rate(self, num: int, den: int) -> str:
        return f"{num}/{den} ({num / den:.1%})" if den else "n/a"

    @property
    def fields(self) -> tuple[int, int]:
        return sum(r.fields_ok for r in self.results), sum(r.fields_expected for r in self.results)

    @property
    def actions(self) -> tuple[int, int]:
        return sum(r.action_ok for r in self.results), len(self.results)

    def markdown(self, title: str) -> str:
        f_ok, f_all = self.fields
        a_ok, a_all = self.actions
        invented = sum(bool(r.stray_numbers) for r in self.results)
        wrong = sum(bool(r.wrong_saves) for r in self.results)
        lines = [f"# {title}", "", f"Mode: `{self.mode}` · cases: {len(self.results)} · "
                 f"passed: {self.rate(sum(r.passed for r in self.results), len(self.results))}", "",
                 "| metric | result | spec target |", "| --- | --- | --- |",
                 f"| field extraction | {self.rate(f_ok, f_all)} | ≥ 95% per field (live LLM) |",
                 f"| right tool / path | {self.rate(a_ok, a_all)} | ≥ 95% (live LLM) |",
                 f"| replies with an invented amount | {invented} | 0 |",
                 f"| turns with a wrong or guessed value | {wrong} | 0 |",
                 f"| dead ends / crashes | {sum(r.dead_end for r in self.results)} / "
                 f"{sum(bool(r.error) for r in self.results)} | 0 |", "",
                 "## By category", "", "| category | cases | passed | fields |", "| --- | ---: | ---: | ---: |"]
        cats: dict[str, list[CaseResult]] = {}
        for r in self.results:
            cats.setdefault(r.category, []).append(r)
        for cat, rs in cats.items():
            lines.append(f"| {cat} | {len(rs)} | {sum(r.passed for r in rs)} | "
                         f"{sum(r.fields_ok for r in rs)}/{sum(r.fields_expected for r in rs)} |")
        lines += ["", "## Cases that did not pass", "",
                  "| id | input | failure category | detail |", "| --- | --- | --- | --- |"]
        for r in self.results:
            if not r.passed:
                detail = "; ".join(filter(None, [r.error, ", ".join(r.wrong_saves),
                                                 f"stray {r.stray_numbers}" if r.stray_numbers else "",
                                                 f"fields {r.fields_ok}/{r.fields_expected}"
                                                 if r.fields_ok < r.fields_expected else "",
                                                 f"path {r.path}, expected {r.action}" if not r.action_ok else "",
                                                 "dead end" if r.dead_end else ""]))
                lines.append(f"| {r.id} | {r.text} | {r.failure_category} | {detail} |")
        return "\n".join(lines) + "\n"
