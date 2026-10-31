"""Code guardrails on every LLM-written reply, before it is sent.

a. numbers: each number appears in STATE, a tool result, the FAQ or the employee's own messages;
b. sources: a sentence that states a rule / eligibility / benefit carries a valid hidden tag ([[faq:id]],
   [[tool:name]] of a tool called this turn, [[state]], or [[catalog]] on a sentence naming a catalog car);
   tags are stripped before display;
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

from app.modules.rules.public import RulesSnapshot, matched_name

from .llm.port import ChatMessage, LLMChain, LLMError
from .parsing import numbers_in

# any hidden tag, so none ever reaches the screen; its content may list several sources ("faq:a,faq:b"), and
# each one is checked (_tag_refs). An unknown form is removed and counts as a missing source (live QA 22/10).
TAG_RE = re.compile(r"\s*\[\[([^\[\]\n]*)\]\]")
_TAG_PART_RE = re.compile(r"(faq|tool|state|catalog)(?::([\w-]+))?")


def _tag_refs(sentence: str) -> list[Optional[tuple[str, str]]]:
    """Every source a sentence's tags name, as (kind, ref); None for a part that is not a known form."""
    out: list[Optional[tuple[str, str]]] = []
    for content in TAG_RE.findall(sentence):
        for part in content.split(","):
            m = _TAG_PART_RE.fullmatch(part.strip())
            out.append((m.group(1), m.group(2) or "") if m else None)
    return out
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
# words that name a profile field a question must not ask once it is saved: more specific than FIELD_WORDS
# ("שכר" alone is in "יש רכיבי שכר נוספים", the generic salary-components question)
STALE_WORDS = {"base": ("שכר בסיס", "משכורת בסיס"), "commissions": ("עמל",), "bonus": ("בונוס",),
               "pension_percent": ("פנסי",), "study_fund_percent": ("השתלמות",), "credit_points": ("זיכוי",),
               "children": ("ילדים", "ילד ")}
VERIFY_PROMPT ="""You verify a reply of a company-car advisor. For each numbered sentence decide whether the FAQ and
STATE below support it (facts, rules, eligibility, amounts). Supported without a source: questions, apologies, saying that the advisor has no information or cannot
help with something (other people's data, personal tax advice, inventing numbers), and offers to ask the
transport representative. An acknowledgement ("רשמתי: …") is supported only if STATE shows that value.
Return JSON only: {"unsupported": [<sentence numbers>]}."""
# a boundary or an honest "no information" is not a policy claim
BOUNDARY_CUES = ("לא יכול", "לא אוכל", "אין לי מידע", "אין לי אפשרות", "אינני יכול", "לא מסוגל", "לא אמסור",
                 "לא ניתן לי", "לא אמציא", "אני לא ממציא", "אין באפשרותי")


# retry feedback for violations whose name alone does not say how to fix them
HINTS = {"repeat": "the question is word for word the previous one; say in one line what is already saved "
                   "(\"רשמתי: …\") and ask only for what is still missing, in other words",
         "unanswered": "the employee asked something or complained: answer it or acknowledge it in one sentence "
                       "before the next question",
         "stale": "a question names a field the employee already answered (STATE knows it): ask only for what "
                  "is still missing (NEXT in STATE)"}


@dataclass
class Violation:
    kind: str
    sentence: Optional[str] = None      # sentence-level violations can be dropped; others need a rewrite

    def __str__(self) -> str:
        if self.kind in HINTS:
            return f"{self.kind}: {HINTS[self.kind]}"
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


# brackets a tag stood in alone ("עמלות חודשיות ([[state]])?" reached the screen as "עמלות חודשיות ()?", 31/10)
_EMPTY_BRACKETS_RE = re.compile(r"\s*[(\[{]\s*[)\]}]")


def strip_tags(text: str) -> str:
    return "\n".join(_EMPTY_BRACKETS_RE.sub("", TAG_RE.sub("", line)).rstrip()
                     for line in _attach_tags(text).splitlines()).strip()


def _is_question(s: str) -> bool:
    return s.rstrip().endswith("?") or s.startswith(CTA_STARTS)


def has_statement(text: str) -> bool:
    """A sentence that is neither a question / call to action nor a "רשמתי" acknowledgement: an answer."""
    return any(not _is_question(c) and not c.startswith("רשמתי")
               for c in (TAG_RE.sub("", s).strip() for s in sentences(text)) if c)


def ask_lines(text: str) -> list[str]:
    """The question lines of a reply, whitespace-normalised: to tell a verbatim repeat."""
    return [" ".join(TAG_RE.sub("", line).split()) for line in text.splitlines() if "?" in line]


def _catalog_supported(sentence: str, snapshot: RulesSnapshot) -> bool:
    """[[catalog]] stands only on a sentence that names a catalog car, or says the model is not in it."""
    return "בקטלוג" in sentence or any(matched_name(sentence, c) for c in snapshot.catalog)


def check(raw: str, *, snapshot: RulesSnapshot, allowed: set[Decimal], tools_called: Iterable[str],
          fields: Sequence[str], offer_rep: bool, waiting_answer: bool, conflicts: Sequence[str],
          must_answer: bool = False, one_field: bool = False, closed: Sequence[str] = ()) -> Check:
    tools = set(tools_called)
    out = Check(strip_tags(raw))
    for s in sentences(raw):
        tags = _tag_refs(s)
        clean = TAG_RE.sub("", s).strip()
        for tag in tags:
            kind, ref = tag if tag is not None else ("", "")
            if tag is None or (kind == "faq" and ref not in snapshot.faq.ids) or (
                    kind == "tool" and ref not in tools) or (
                    kind == "catalog" and not _catalog_supported(clean, snapshot)):
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
    # exactly one call to action: the last line; no other line asks or instructs
    asks = sum(1 for line in lines if line.strip().startswith(CTA_STARTS) or "?" in line)
    if asks != 1 or not _is_question(lines[-1].strip().rstrip(",.")):
        out.violations.append(Violation("cta"))
    # one request per reply: the asking line never mixes the salary details with the car, and asks a
    # single field when the employee asked for one question at a time
    named = [f for f in fields if any(w in lines[-1] for w in FIELD_WORDS.get(f, ()))]
    car_details = {"copay", "use_value", "drive", "maintenance", "car_choice"}   # "רכב" alone is everywhere
    salary = {"base", "commissions", "bonus", "pension_percent", "study_fund_percent", "credit_points", "children"}
    if (one_field and len(named) > 1) or (car_details & set(named) and salary & set(named)):
        out.violations.append(Violation("multi"))
    if closed:
        # a question never names a field the employee already answered (STATE knows it)
        asking = [ln for ln in lines if "?" in ln or ln.strip().startswith(CTA_STARTS)]
        if any(w in ln for ln in asking for f in closed for w in STALE_WORDS.get(f, ())):
            out.violations.append(Violation("stale"))
    if offer_rep and ("נציג" not in lines[-1] or text.count("?") > 1):
        out.violations.append(Violation("offer"))     # the offer is the only question, and the last line
    elif fields and not waiting_answer and not any(w in text for f in fields for w in FIELD_WORDS.get(f, ())):
        out.violations.append(Violation("progress"))
    if conflicts and "?" not in text:
        out.violations.append(Violation("conflict"))
    if must_answer and not has_statement(text):
        # a question / "מה?" / a complaint gets an answer or an acknowledgement: the flow question alone
        # (even a compound one, with several "?" sentences) is not one
        out.violations.append(Violation("unanswered"))
    return out


def content_sentences(raw: str) -> list[str]:
    """Sentences beyond the acknowledgement and the question: the ones the claim verifier checks."""
    out = []
    for s in sentences(raw):
        clean = TAG_RE.sub("", s).strip()
        if not clean or _is_question(clean) or any(b in clean for b in BOUNDARY_CUES):
            continue
        if clean.startswith("רשמתי"):        # an acknowledgement must match what was saved (STATE)
            out.append(clean)
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
