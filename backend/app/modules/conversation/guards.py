"""Code guardrails on every LLM-written reply, before it is sent.

a. numbers: each number appears in STATE, a tool result, the FAQ or the employee's own messages;
b. sources: a sentence that states a rule / eligibility / benefit carries a valid hidden tag ([[faq:id]],
   [[tool:name]] of a tool called this turn, or [[state]]); tags are stripped before display;
c. claims: a short second call checks content sentences against the FAQ and STATE (claim verifier);
d. progress: while data is missing the reply asks for a missing item (or offers the representative);
e. shape: line count, one call to action, plural address, no "!" / emojis / banned phrases / openers;
g. a number the parser and the LLM read differently must be asked about.
(f, no silent drop, is enforced on the tool calls themselves: agent._check_call.)
On a violation the agent retries once with the violations as feedback, then drops the offending sentences
if the rest still stands, otherwise sends the template.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from app.modules.rules.public import RulesSnapshot

from .llm.port import ChatMessage, LLMChain, LLMError
from .parsing import numbers_in

TAG_RE = re.compile(r"\s*\[\[(faq|tool|state)(?::([\w-]+))?\]\]")
SINGULAR_RE = re.compile(r"(?<![א-ת])(?:שלך|לך|ממך|אותך|עבורך|בחרת|כתבת|התכוונת|ביקשת|תרצה|תרצי)(?![א-ת])")
OPENERS = ("בשמחה", "שמנו לב", "איזה יופי", "רשמנו", "מעולה", "נהדר", "כדי שנוכל להתקדם", "היי", "תודה רבה")
BANNED = ("לא הצלחתי להבין את התשובה", "אני רק בוט", "אני רק מודל", "כבינה מלאכותית")
CTA_STARTS = ("כתבו", "בחרו", "אפשר לכתוב")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")
MAX_LINES = 6
# words that make a sentence a statement of company policy / eligibility / benefits (needs a source)
POLICY_WORDS = ("זכאי", "זכאות", "מותר", "אסור", "חובה", "תקציב", "החזר", "מסלול", "ויתור", "לפי הכללים",
                "לפי כללי", "מגיע לכם", "החברה משלמת", "המעסיק", "שווה ", "מס הכנסה", "מקבלים")
# words that tie a reply to each field it may ask for (progress check)
FIELD_WORDS = {"occupation": ("קבוצ", "תפקיד"), "group": ("קבוצ",), "opt_out_occupation": ("תפקיד",),
               "base": ("שכר",), "commissions": ("עמל",), "bonus": ("בונוס",), "pension_percent": ("פנסי",),
               "study_fund_percent": ("השתלמות",), "credit_points": ("זיכוי",), "children": ("ילד",),
               "car": ("רכב", "דגם"), "copay": ("השתתפות", "משלמ"), "use_value": ("שווי",),
               "drive": ("הנעה", "חשמלי", "היברידי", "בנזין"), "maintenance": ("אחזק",),
               "car_choice": ("גרס", "מספר"), "options": ("רכב", "השוו", "חלופ")}
VERIFY_PROMPT = """You verify a reply of a company-car advisor. For each numbered sentence decide whether the FAQ and
STATE below support it (facts, rules, eligibility, amounts). Supported without a source: questions,
acknowledgements of what the employee said, apologies, saying that the advisor has no information or cannot
help with something (other people's data, personal tax advice, inventing numbers), and offers to ask the
transport representative. Return JSON only: {"unsupported": [<sentence numbers>]}."""
# a boundary or an honest "no information" is not a policy claim
BOUNDARY_CUES = ("לא יכול", "לא אוכל", "אין לי מידע", "אין לי אפשרות", "אינני יכול", "לא מסוגל", "לא אמסור",
                 "לא ניתן לי", "לא אמציא", "אני לא ממציא", "אין באפשרותי")


@dataclass
class Violation:
    kind: str
    sentence: Optional[str] = None      # sentence-level violations can be dropped; others need a rewrite

    def __str__(self) -> str:
        return f"{self.kind}: {self.sentence}" if self.sentence else self.kind


@dataclass
class Check:
    text: str                                      # the reply with the tags removed
    violations: list[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


_TAG_AFTER_RE = re.compile(r"([.?!])([ \t]*)((?:\[\[[^\]]*\]\][ \t]*)+)")


def _attach_tags(text: str) -> str:
    """"…בחודש. [[faq:x]]" → "…בחודש [[faq:x]]." so a tag stays with its sentence."""
    return _TAG_AFTER_RE.sub(lambda m: " " + m.group(3).strip() + m.group(1), text)


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.?])\s+|\n+", _attach_tags(text)) if s.strip()]


def strip_tags(text: str) -> str:
    return "\n".join(TAG_RE.sub("", line).rstrip() for line in _attach_tags(text).splitlines()).strip()


def _is_question(s: str) -> bool:
    return s.rstrip().endswith("?") or s.startswith(CTA_STARTS)


def check(raw: str, *, snapshot: RulesSnapshot, allowed: set[Decimal], tools_called: Iterable[str],
          fields: Sequence[str], offer_rep: bool, waiting_answer: bool, conflicts: Sequence[str],
          must_answer: bool = False) -> Check:
    tools = set(tools_called)
    out = Check(strip_tags(raw))
    for s in sentences(raw):
        tags = TAG_RE.findall(s)
        clean = TAG_RE.sub("", s).strip()
        for kind, ref in tags:
            if (kind == "faq" and ref not in snapshot.faq.ids) or (kind == "tool" and ref not in tools):
                out.violations.append(Violation("source", clean))
        if not tags and not _is_question(clean) and not clean.startswith("רשמתי") and \
                not any(b in clean for b in BOUNDARY_CUES) and any(w in clean for w in POLICY_WORDS):
            out.violations.append(Violation("source", clean))
        bad = [n for n in numbers_in(clean) if abs(n) not in allowed]
        if bad:
            out.violations.append(Violation("number", clean))
    text = out.text
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        out.violations.append(Violation("empty"))
        return out
    if len(lines) > MAX_LINES:
        out.violations.append(Violation("length"))
    if SINGULAR_RE.search(text):
        out.violations.append(Violation("address"))
    if "!" in text:                                  # only the fixed rep message (a template) has "תודה!"
        out.violations.append(Violation("exclamation"))
    if EMOJI_RE.search(text):
        out.violations.append(Violation("emoji"))
    if any(b in text for b in BANNED):
        out.violations.append(Violation("banned"))
    if lines[0].strip().startswith(OPENERS):
        out.violations.append(Violation("opener"))
    if sum(1 for line in lines if line.strip().startswith(CTA_STARTS)) > 1 or not _is_question(lines[-1].strip()):
        out.violations.append(Violation("cta"))
    if offer_rep and "נציג" not in text:
        out.violations.append(Violation("offer"))
    elif fields and not waiting_answer and not any(w in text for f in fields for w in FIELD_WORDS.get(f, ())):
        out.violations.append(Violation("progress"))
    if conflicts and "?" not in text:
        out.violations.append(Violation("conflict"))
    if must_answer and len(sentences(text)) < 2:
        out.violations.append(Violation("unanswered"))   # a question / "מה?" / a complaint gets more than the CTA
    return out


def content_sentences(raw: str) -> list[str]:
    """Sentences beyond the acknowledgement and the question: the ones the claim verifier checks."""
    out = []
    for s in sentences(raw):
        clean = TAG_RE.sub("", s).strip()
        if not clean or _is_question(clean) or clean.startswith("רשמתי") or any(b in clean for b in BOUNDARY_CUES):
            continue
        # only sentences that can carry a claim: a source tag, a policy word or a number
        if TAG_RE.search(s) or any(w in clean for w in POLICY_WORDS) or numbers_in(clean):
            out.append(clean)
    return out


def verify(llm: LLMChain, snapshot_text: str, state_text: str, items: Sequence[str],
           deadline: Optional[float]) -> Optional[list[str]]:
    """The claim verifier. Returns the unsupported sentences, or None when it could not run in time."""
    if not items:
        return []
    numbered = "\n".join(f"{i}. {s}" for i, s in enumerate(items, 1))
    try:
        response = llm.complete([ChatMessage("system", VERIFY_PROMPT),
                                 ChatMessage("user", f"{snapshot_text}\n\n{state_text}\n\nREPLY:\n{numbered}")],
                                deadline=deadline)
        data = json.loads(re.search(r"\{.*\}", response.text, re.S).group(0))     # type: ignore[union-attr]
        return [items[i - 1] for i in data.get("unsupported", []) if isinstance(i, int) and 1 <= i <= len(items)]
    except (LLMError, AttributeError, ValueError, TypeError):
        return None


def drop(text: str, bad: Iterable[str]) -> str:
    """The reply without the offending sentences (kept in their lines otherwise)."""
    bad = {b for b in bad if b}
    kept = []
    for line in _attach_tags(text).splitlines():
        parts = [s for s in sentences(line) if TAG_RE.sub("", s).strip() not in bad]
        if parts:
            kept.append(" ".join(parts))
    return "\n".join(kept)


__all__ = ["Check", "VERIFY_PROMPT", "Violation", "check", "content_sentences", "drop", "sentences",
           "strip_tags", "verify", "SINGULAR_RE"]
