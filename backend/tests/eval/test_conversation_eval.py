"""Live conversation eval with a calibrated judge.

    pytest -m eval tests/eval/test_conversation_eval.py      (EVAL_TURN_PAUSE_S=2 by default; paid tier)

1. Judge calibration: 15 hand-labelled turns (calibration.yaml). The judge (EVAL_JUDGE_MODEL, default
   gemini-3.5-flash-lite; gemini-3.6-flash only if the first one fails) must agree with ≥ 14 of 15.
2. The 30 cases (conversations.yaml) run through the service with the live turn model (EVAL_TURN_MODEL).
   Every bot turn is judged on six criteria; code asserts the number guard, no dropped field and the final net.
3. eval-report.md: scores per criterion, latency p50/p95, LLM-vs-template counts, the failing turns.
Before anything runs, the estimated number of LLM calls is printed; above EVAL_MAX_CALLS (1,000) it stops.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.modules.conversation.guard import collect_numbers
from app.modules.conversation.llm.port import ChatMessage, LLMError
from app.modules.conversation.parsing import numbers_in
from app.modules.conversation.public import ConversationService, build_llm_chain
from app.modules.conversation.turn import render_faq, state_block
from app.modules.rules.public import RulesStore
from app.shared.settings import Settings

from ..conftest import CONFIG, build_engine

pytestmark = pytest.mark.eval

HERE = Path(__file__).parent
REPORT = HERE.parent.parent / "eval-report.md"
PAUSE_S = float(os.environ.get("EVAL_TURN_PAUSE_S", "2"))
TURN_MODEL = os.environ.get("EVAL_TURN_MODEL", "gemini-3.5-flash-lite")
FALLBACK_MODEL = os.environ.get("EVAL_FALLBACK_MODEL", "gemini-3.6-flash")
JUDGES = [os.environ.get("EVAL_JUDGE_MODEL", "gemini-3.5-flash-lite"), "gemini-3.6-flash"]
MAX_CALLS = int(os.environ.get("EVAL_MAX_CALLS", "1000"))
CALLS_PER_TURN = 2.6        # tool round + reply round + (verifier, retry) on some turns
CRITERIA = ("answered_what_was_asked", "correct_per_state_and_faq", "no_invention", "concise",
            "natural_professional_hebrew", "single_cta")

JUDGE_PROMPT = """You grade ONE reply of a Hebrew company-car advisor bot at Strauss. Be strict and fair.
You get the FAQ (the only source of company rules), STATE (what the system knows after this turn), the last
messages and the REPLY. Grade each criterion with ok true/false and a one-line reason:
- answered_what_was_asked: if the employee's last message asks, complains or requests something, the reply
  addresses it first (an honest "I have no information" plus an offer to ask the representative counts; a
  polite one-line boundary counts for off-topic, injection, tax advice or other people's data). If the message
  only gave data, the reply acknowledges it.
- correct_per_state_and_faq: nothing contradicts STATE or the FAQ; it never asks for something STATE already
  knows; what it asks next is missing in STATE (or a pending confirmation / an offer).
- no_invention: no number, rule, eligibility, benefit or promise beyond STATE, the FAQ and the employee's words.
- concise: at most about 5 short lines (a results summary may be longer); no repetition.
- natural_professional_hebrew: natural, warm, professional Hebrew in second-person plural; no robotic phrases
  such as "לא הצלחתי להבין את התשובה"; no emojis.
- single_cta: it ends with exactly one clear call to action (one question or one instruction). One question
  that names several related missing items (e.g. the salary components) is one call to action; after a
  result, one offered next step is the call to action.
Return JSON only, e.g. {"answered_what_was_asked": {"ok": true, "reason": "..."}, ...} with all six keys."""

SETTINGS = Settings()


def chain(model: str, fallback: str = "", judge: bool = False):
    entries = f"gemini:{model}" + (f",gemini:{fallback}" if fallback else "")
    if judge:      # long prompts: no 4 s per-call cap, and a slow call does not bench the judge for a minute
        return build_llm_chain(Settings(llm_chain=entries, llm_timeout_s=20, llm_cooldown_fail_s=0,
                                        llm_cooldown_429_s=5))
    return build_llm_chain(Settings(llm_chain=entries))


def load(name: str) -> Any:
    return yaml.safe_load((HERE / name).read_text(encoding="utf-8"))


CASES = load("conversations.yaml")["cases"]
CALIBRATION = load("calibration.yaml")["items"]


# ---------- the reference employees as messages ----------

def employee_turns(emp_id: str) -> tuple[list[str], Any]:
    from ..scenarios.test_styles import EMPLOYEES, TYPED_OCCUPATION, labelled, num

    emp = next(e for e in EMPLOYEES if e.id == emp_id)
    v = emp.values
    profile = ", ".join([f"אני {TYPED_OCCUPATION[emp.occupation]}", labelled("base", v["base"]),
                         *(labelled(f, v[f]) for f in ("commissions", "bonus", "pension_percent",
                                                       "study_fund_percent", "credit_points", "children"))])
    car = (f"יש לי {emp.typed_car(emp.car)} ואני משתתפת ב-{num(v['copay'])} ש\"ח בחודש" if emp.car
           else f"אין לי רכב, אני מקבלת אחזקה {num(v['maintenance'], True)} ש\"ח בחודש")
    alternative = ("אני רוצה לוותר על הרכב" if emp.alternative == "OPT_OUT"
                   else f"אני רוצה לבדוק את {emp.typed_car(emp.alternative)}")
    return [profile, car, alternative], emp


def turns_of(case: dict) -> list[Any]:
    return employee_turns(case["employee"])[0] if "employee" in case else case["turns"]


def estimate() -> int:
    turns = sum(len([t for t in turns_of(c) if isinstance(t, str)]) for c in CASES)
    return int(turns * CALLS_PER_TURN) + turns + 2 * len(CALIBRATION)     # turns + judge + calibration (×2 worst)


# ---------- the judge ----------

def judge(llm, state: str, last_bot: str, user: str, reply: str, faq: str) -> dict[str, dict]:
    content = (f"{faq}\n\nSTATE:\n{state}\n\nBOT BEFORE:\n{last_bot}\n\nEMPLOYEE:\n{user}\n\nREPLY:\n{reply}")
    for _ in range(3):
        try:
            text = llm.complete([ChatMessage("system", JUDGE_PROMPT), ChatMessage("user", content)]).text
            data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))     # type: ignore[union-attr]
            return {k: {"ok": bool(data.get(k, {}).get("ok")), "reason": str(data.get(k, {}).get("reason", ""))}
                    for k in CRITERIA}
        except (LLMError, AttributeError, ValueError):
            time.sleep(2)
    return {k: {"ok": False, "reason": "judge failed"} for k in CRITERIA}


def calibrate(faq: str) -> tuple[str, int, list[str]]:
    for model in JUDGES:
        llm = chain(model, judge=True)
        agree, misses = 0, []
        for item in CALIBRATION:
            verdict = judge(llm, item["state"], item["bot"], item["user"], item["reply"], faq)
            passed = all(v["ok"] for v in verdict.values())
            if passed == (item["expected"] == "pass"):
                agree += 1
            else:
                misses.append(f"{item['id']}: expected {item['expected']}, judge "
                              f"{'pass' if passed else 'fail'} ({[k for k, v in verdict.items() if not v['ok']]})")
        if agree >= 14:
            return model, agree, misses
    return model, agree, misses


# ---------- running a case ----------

def run_case(case: dict, turn_llm, judge_llm, faq: str) -> dict[str, Any]:
    from sqlalchemy.orm import sessionmaker

    sessions = sessionmaker(bind=build_engine())
    store = RulesStore(CONFIG)
    svc = ConversationService(sessions, store, turn_llm)
    snap = store.current
    turn, token = svc.start()
    cid, last_bot, users = turn.conversation_id, turn.reply, []
    out: dict[str, Any] = {"id": case["id"], "turns": [], "asserts": []}
    turns, emp = (employee_turns(case["employee"]) if "employee" in case else (case["turns"], None))
    for message in turns:
        if isinstance(message, dict):                          # the representative answers
            [q] = [q for q in svc.cases.list_open() if q.missing_field.value == "NEW_CAR_COPAY"] or [None]
            if q is None:
                out["asserts"].append("no open copay question for the representative")
                continue
            svc.cases.answer(q.id, message["rep_answer"])
            continue
        users.append(message)
        started = time.monotonic()
        turn = svc.send(cid, token, message)
        latency = time.monotonic() - started
        state = svc.view(cid, token).state
        allowed = set().union(collect_numbers(state.model_dump(mode="json")), collect_numbers(users),
                              collect_numbers([e.answer for e in snap.faq.entries]),
                              collect_numbers([c.label for c in snap.catalog]), {Decimal(3), Decimal(12), Decimal(13)})
        facts = re.split(r"למשל", turn.reply)[0] if "למשל" in turn.reply else turn.reply   # examples are not facts
        stray = [str(n) for n in numbers_in(facts) if abs(n) not in allowed]
        verdict = judge(judge_llm, state_block(state, snap), last_bot, message, turn.reply, faq)
        out["turns"].append({"user": message, "reply": turn.reply, "source": turn.source, "say": turn.say,
                             "latency": latency, "verdict": verdict, "stray_numbers": stray})
        if stray:
            out["asserts"].append(f"number guard: {stray} in {turn.reply[:80]!r}")
        last_bot = turn.reply
        time.sleep(PAUSE_S)
    state = svc.view(cid, token).state
    p, c = state.profile, state.current
    values = {**{k: getattr(p, k) for k in ("base", "commissions", "bonus", "pension_percent", "study_fund_percent",
                                            "credit_points", "children")}, "copay": c.copay}
    for k, v in (case.get("saved") or {}).items():
        if values.get(k) is None or Decimal(str(values[k])) != Decimal(str(v)):
            out["asserts"].append(f"dropped or wrong field: {k}={values.get(k)} (expected {v})")
    for k in case.get("kept_empty") or []:
        if values.get(k) is not None:
            out["asserts"].append(f"guessed field: {k}={values[k]}")
    if case.get("results") or emp is not None:
        if not state.results:
            out["asserts"].append("no result at the end")
    if emp is not None and state.results:
        r = next((x.result for x in state.results if x.label.startswith(emp.alt_label.split(" (")[0])), None)
        if r is None or (Decimal(r["today"]["net_display"]), Decimal(r["alternative"]["net_display"])) != (
                emp.net_today, emp.net_alternative):
            out["asserts"].append(f"final net differs from the engine reference ({emp.id})")
    if case.get("copay") and state.results:
        used = state.results[0].result.get("alternative", {}).get("copay")
        if used is None or Decimal(str(used)) != Decimal(case["copay"]):
            out["asserts"].append(f"the representative's copay was not used: {used}")
    return out


def write_report(model: str, judge_model: str, agree: int, misses: list[str], results: list[dict]) -> dict:
    turns = [t for r in results for t in r["turns"]]
    scores = {k: sum(t["verdict"][k]["ok"] for t in turns) / max(1, len(turns)) for k in CRITERIA}
    latencies = sorted(t["latency"] for t in turns)
    p95 = latencies[min(len(latencies) - 1, int(0.95 * len(latencies)))] if latencies else 0
    sources: dict[str, int] = {}
    says: dict[str, int] = {}
    for t in turns:
        sources[t["source"]] = sources.get(t["source"], 0) + 1
        says[t["say"]] = says.get(t["say"], 0) + 1
    net_ok = not any("net" in a or "copay" in a for r in results for a in r["asserts"])
    lines = [f"# Conversation eval — turn model {model}", "",
             f"Judge: {judge_model}, calibration {agree}/15" + (" (counts)" if agree >= 14 else " (NOT calibrated)"),
             *(f"- calibration miss: {m}" for m in misses), "",
             f"Cases: {len(results)}, bot turns: {len(turns)}", "", "| criterion | score |", "|---|---|",
             *(f"| {k} | {v:.1%} |" for k, v in scores.items()),
             f"| final net / rep copay correct | {'100%' if net_ok else 'NO'} |", "",
             f"Latency p50 {statistics.median(latencies) if latencies else 0:.2f} s, p95 {p95:.2f} s", "",
             f"Sources: {sources}", f"SAY / guard outcomes: {says}", "", "## Code assertions"]
    lines += [f"- {r['id']}: {a}" for r in results for a in r["asserts"]] or ["- none"]
    lines += ["", "## Failing turns"]
    for r in results:
        for i, t in enumerate(r["turns"], 1):
            bad = {k: v["reason"] for k, v in t["verdict"].items() if not v["ok"]}
            if bad:
                lines += [f"### {r['id']} turn {i} ({t['source']}, {t['say']})", f"U: {t['user']}",
                          "B: " + t["reply"].replace("\n", " / "), *(f"- {k}: {v}" for k, v in bad.items()), ""]
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    return {"scores": scores, "p95": p95, "net_ok": net_ok}


def test_conversation_eval():
    if not build_llm_chain(SETTINGS).available:
        pytest.skip("no LLM key")
    calls = estimate()
    print(f"\nestimated LLM calls for this run: {calls}")
    if calls > MAX_CALLS:
        pytest.fail(f"estimated {calls} LLM calls > {MAX_CALLS}: ask before running")
    store = RulesStore(CONFIG)
    faq = render_faq(store.current)
    judge_model, agree, misses = calibrate(faq)
    print(f"judge {judge_model}: calibration {agree}/15")
    turn_llm, judge_llm = chain(TURN_MODEL, FALLBACK_MODEL), chain(judge_model, judge=True)
    only = os.environ.get("EVAL_ONLY", "")
    results = [run_case(c, turn_llm, judge_llm, faq) for c in CASES if not only or c["id"] in only.split(",")]
    summary = write_report(TURN_MODEL, judge_model, agree, misses, results)
    print(json.dumps(summary, ensure_ascii=False, default=str))
    assert agree >= 14, f"judge not calibrated ({agree}/15): {misses}"
