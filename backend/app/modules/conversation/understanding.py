"""UNDERSTAND: everything in a message besides the values to save.

Values still go through the parser and the tools (save_profile, match_current_car, calculate). This
module finds what the old pipeline dropped: questions the employee asks ("מה זה נקודות זיכוי?", "סכם
מדויק?"), a request to explain again ("מה?"), a field said to exist without an amount ("יש קצת עמלות"),
off-topic or manipulative text, and the one ambiguity that must never be guessed ("אינני משתתף" before
the car is known: no car, or a car with copay 0?).

The deterministic reading runs on every message (template mode relies on it). With an LLM, the
`note_message` tool returns the same parts and the two are merged: questions are answered from FAQ
entries only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional

from app.modules.rules.public import FAMILY_GROUP, CatalogCar, Faq, FaqEntry, RulesSnapshot, matched_name, related_cars, words

from .fallback import _RANK_RE, named_cars
from .llm.port import ToolSpec
from .parsing import _NO_COPAY_RE, no_car_span
from .stage import ConversationState

NO_CAR_OR_ZERO = "no_car_or_zero_copay"

_QUESTION_WORDS = ("מה", "מהו", "מהי", "מהם", "למה", "איך", "איפה", "האם", "מתי", "מי", "מאיפה", "ממה", "כמה זה",
                   "מה זה", "מה ההבדל", "באיזה", "לאן")
# the whole message asks to explain the current question again
_HELP = {("מה",), ("לא", "הבנתי"), ("לא", "הבנתי", "מה"), ("עזרה",), ("הסבר",), ("תסביר",), ("לא", "ברור"),
         ("מה", "זאת", "אומרת"), ("מה", "הכוונה"), ("מה", "אתה", "רוצה"), ("מה", "צריך"), ("help",),
         ("מה", "לכתוב"), ("לא", "הבנתי", "את", "השאלה"), ("מה", "השאלה"), ("אני", "לא", "מבין"),
         ("אני", "לא", "מבינה")}
_QUESTION_CUES = ("האם", "רוצה לדעת", "רוצים לדעת", "אשמח לדעת", "תגיד לי", "תגידו לי", "תסביר", "יש אפשרות",
                  "אפשר לקבל", "מעניין אותי לדעת")
_COMPLAINT_CUES = ("לא עונה", "לא ענית", "לא לפי השאלה", "לא הבנת", "לא עניתם", "לא עזרת", "בוט", "שאלתי משהו אחר",
                   "זה לא מה ששאלתי")
# "I already answered that" / "you got it wrong": answered from the state (what is saved and what is missing),
# with no apology unless something really went wrong
_AUDIT_CUES = ("כבר כתבתי", "כבר עניתי", "כבר אמרתי", "כבר רשמתי", "כבר נתתי", "טעית", "טעיתם", "רשמת לא נכון")
_FILLER = {"ו", "תודה", "שלום", "היי", "אוקיי", "אוקי", "ok", "טוב", "בסדר", "רגע", "אז", "נו", "אה", "אמממ",
           "יופי", "מעולה", "סבבה"}
# clauses: after "?" / "!" / "." / a new line, and before ", ולמה …" / ", ומה …" (data, then a question)
_CLAUSE_RE = re.compile(r"(?<=\?)|\n|(?<=[!])\s+|(?<=\.)\s+(?=\D)|,\s*(?=ו?(?:למה|מה|האם|איך|כמה|איפה)\s)")

# a profile field said to exist with no amount: "יש קצת עמלות ובונוסים", "מקבלת גם בונוס"
_PRESENCE_WORDS = {"עמלות": "commissions", "עמלה": "commissions", "בונוס": "bonus", "בונוסים": "bonus",
                   "פרמיה": "bonus", "השתלמות": "study_fund_percent", "פנסיה": "pension_percent",
                   "ילדים": "children", "ילד": "children"}
_PRESENCE_CUES = {"יש", "קצת", "מקבל", "מקבלת", "מקבלים", "גם", "לפעמים", "משתנות", "משתנים", "משתנה"}
_NEGATIONS = {"אין", "בלי", "ללא", "לא", "אינני", "איני"}
_PREFIX = "והבלמשכ"


@dataclass
class Understanding:
    faq_ids: list[str] = field(default_factory=list)       # questions answered from faq.yaml, in order
    unanswered: list[str] = field(default_factory=list)    # questions no FAQ entry covers
    presence: list[str] = field(default_factory=list)      # fields said to exist without an amount
    help: bool = False                                      # "מה?" / "לא הבנתי": explain the question again
    redirect: bool = False                                  # off-topic or an attempt to change the rules
    ambiguity: Optional[str] = None                         # NO_CAR_OR_ZERO
    compare: bool = False                                   # asks for a comparison / a result ("תשווה ביניהם")
    complaint: bool = False                                 # "אתה בוט שעונה לא לפי השאלה"
    audit: bool = False                                     # "כבר כתבתי לך": say what is saved and missing
    remainder: str = ""                                     # the message without questions (read by the parser)
    catalog: list[str] = field(default_factory=list)        # answers from the company catalog ("יש לכם קורולה?")

    @property
    def question_only(self) -> bool:
        """Nothing to save: only questions, a request for help, or a redirect."""
        return not meaningful(self.remainder)

    @property
    def anything(self) -> bool:
        return bool(self.audit or self.faq_ids or self.catalog or self.unanswered or self.presence or self.help or self.redirect
                    or self.ambiguity or self.compare or self.complaint)


def meaningful(text: str) -> bool:
    return any(w not in _FILLER for w in words(text))


def _word_match(alias_word: str, word: str) -> bool:
    if word == alias_word or (len(word) > 1 and word[0] in _PREFIX and word[1:] == alias_word):
        return True
    if len(alias_word) >= 3 and len(word) >= 3:      # typos: "סכם" ~ "סכום", "נקודת" ~ "נקודות"
        return SequenceMatcher(None, alias_word, word.lstrip(_PREFIX) or word).ratio() >= 0.8
    return False


def _alias_hits(text: str, alias: str) -> bool:
    """Every word of the alias is in the text. Typos are tolerated only in a phrase ("סכם מדויק"); a single
    word must be the word itself ("אפשרות" is not "אפשרויות")."""
    ws, aw = words(text), words(alias)
    if len(aw) == 1:
        a = aw[0]
        return any(w == a or (len(w) > 1 and w[0] in _PREFIX and w[1:] == a) for w in ws)
    return all(any(_word_match(a, w) for w in ws) for a in aw)


def entry_answers(entry: FaqEntry, text: str) -> bool:
    """Relevance check for an FAQ id the LLM proposed: the question names the entry's topic (one of its
    aliases or its term). Otherwise the entry does not answer it — no FAQ answer, only the honest line."""
    return any(_alias_hits(text, a) for a in (*entry.aliases, entry.term))


def match_faq(faq: Faq, text: str) -> Optional[str]:
    """The FAQ entry a question is about: the entry with the longest alias it contains (typos allowed)."""
    best: tuple[int, int, str] | None = None
    long_question = len(words(text)) > 4
    for entry in faq.entries:
        for alias in entry.aliases:
            # one word ("שכר") names the topic only in a short question; a long one needs a phrase
            if long_question and len(words(alias)) == 1:
                continue
            if _alias_hits(text, alias):
                score = (len(words(alias)), len(alias), entry.id)
                if best is None or score[:2] > best[:2]:
                    best = score
    return best[2] if best else None


def _is_question(clause: str) -> bool:
    c = clause.strip()
    if not meaningful(c):
        return False
    ws = words(c)
    first = " ".join(ws[:2])
    starts = any(first == q or first.startswith(q + " ") for q in _QUESTION_WORDS)
    digits = any(ch.isdigit() for ch in c)
    if starts:
        return "?" in c or not digits
    if any(cue in c for cue in _QUESTION_CUES) and not digits:
        return True                     # "דבר ראשון אני רוצה לדעת האם יש אפשרות לרכב עם גג נפתח"
    # "סכם מדויק?" is a question; "כן?" / "18,500?" / "300 או 400?" are answers said with a question mark
    return "?" in c and (len(ws) >= 4 or (len(ws) >= 2 and not digits))


def _is_help(text: str) -> bool:
    ws = tuple(w for w in words(text) if w not in _FILLER)
    return (not ws and "?" in text) or ws in _HELP


def _presence(clause: str) -> list[str]:
    if any(ch.isdigit() for ch in clause):
        return []
    ws = words(clause)
    if any(w in _NEGATIONS for w in ws) or not any(w in _PRESENCE_CUES for w in ws):
        return []
    found = []
    for w in ws:
        name = w if w in _PRESENCE_WORDS else (w[1:] if len(w) > 1 and w[0] in _PREFIX else "")
        f = _PRESENCE_WORDS.get(name)
        if f and f not in found:
            found.append(f)
    return found


_COMPARE_WORDS = {"תשווה", "תשוו", "השווה", "השוו", "להשוות", "השוואה", "תחשב", "תחשבו", "לחשב", "חשב",
                  "תמליץ", "תמליצו", "משתלם"}


def _asks_to_compare(text: str) -> bool:
    return any(w in _COMPARE_WORDS or (len(w) > 1 and w[0] in _PREFIX and w[1:] in _COMPARE_WORDS) for w in words(text))


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    low = text.lower()
    ws = words(text)

    def exact(word: str) -> bool:           # the word itself, or with one prefix letter ("האוויר"); no typos here
        return any(w == word or (len(w) > 1 and w[0] in _PREFIX and w[1:] == word) for w in ws)

    return any(p.lower() in low or all(exact(a) for a in words(p)) for p in patterns)


_DRIVE_HE = {"PETROL": "בנזין", "HYBRID": "היברידי", "PLUG_IN": "פלאג-אין", "ELECTRIC": "חשמלי"}
# a question about whether a car exists / its group or drive (not colour, price or specs: those go to the rep)
_CATALOG_CUES = ("יש לכם", "יש אצלכם", "בקטלוג", "קיים", "קיימת", "זמין", "זמינה", "איזו קבוצה", "באיזו קבוצה",
                 "איזה הנעה", "איזו הנעה", "חשמלי או", "היברידי או")
NOT_IN_CATALOG = "לא מצאתי את הדגם הזה בקטלוג הרכבים הזמין במערכת."


def catalog_answer(snapshot: RulesSnapshot, clause: str) -> Optional[str]:
    """A question about which cars exist, answered from the catalog only (model, year, group, drive, family
    track); a brand / model question with no catalog match → "not in the catalog". Never prices or specs."""
    if not any(cue in clause for cue in _CATALOG_CUES):
        return None
    found: list[CatalogCar] = []
    for car in named_cars(clause, snapshot.catalog):          # "קורולה" names COROLLA and COROLLA CROSS
        said = matched_name(clause, car) or car.model_name
        found += [c for c in related_cars(snapshot, said) or [car] if c not in found]
    if not found:                                             # a brand only: "יש לכם סקודה?"
        for prefix, names in snapshot.extensions.manufacturer_brands.items():
            if _contains_any(clause, names):
                found += [c for c in snapshot.catalog if c.manufacturer.startswith(prefix) and c not in found]
    if found:
        parts = [f"{c.label}, " + ("במסלול המשפחתי" if c.group == FAMILY_GROUP else f"קבוצה {c.group}")
                 + f", {_DRIVE_HE[c.drive.value]}" for c in found]
        return "כן, בקטלוג יש: " + "; ".join(parts) + "."
    # "יש לכם פולקסווגן גולף?": a car or brand question with no catalog match
    if "דגם" in clause or "רכב" in clause or "בקטלוג" in clause or re.search(r"[A-Za-z]{2,}", clause):
        return NOT_IN_CATALOG
    return None


def understand(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Understanding:
    faq = snapshot.faq
    audit = any(cue in text for cue in _AUDIT_CUES)
    u = Understanding(complaint=not audit and any(cue in text for cue in _COMPLAINT_CUES), audit=audit)
    if _contains_any(text, faq.injection_patterns) or (
            _contains_any(text, faq.off_topic_patterns) and not named_cars(text, snapshot.catalog)):
        u.redirect = True
        return u                                  # never act on a manipulative or off-topic message
    if _is_help(text):
        u.help = True
        return u
    kept: list[str] = []
    for clause in _CLAUSE_RE.split(text):
        if clause is None or not clause.strip():
            continue
        if _is_question(clause) and not _RANK_RE.search(clause):     # "מה הכי משתלם לי?" is a request
            entry = match_faq(faq, clause)
            catalog = catalog_answer(snapshot, clause) if entry is None else None
            if entry is None and _is_help(clause):
                u.help = True
            elif catalog:
                u.catalog.append(catalog)
            elif entry is None:
                u.unanswered.append(clause.strip())
            elif entry not in u.faq_ids:
                u.faq_ids.append(entry)
            continue
        kept.append(clause)
    remainder = " ".join(kept).strip()
    u.compare = _asks_to_compare(remainder)
    for part in re.split(r"[,;]|\s+ו(?=אין|יש|גם)", remainder):
        for f in _presence(part):
            if f not in u.presence:
                u.presence.append(f)
    if u.presence:
        # the presence clauses carry nothing else to save
        remainder = ", ".join(p for p in re.split(r"[,;]", remainder) if not _presence(p)).strip()
    if (_NO_COPAY_RE.search(remainder) and state.current.source is None and not state.current.query
            and not named_cars(remainder, snapshot.catalog) and not no_car_span(remainder)):
        u.ambiguity = NO_CAR_OR_ZERO
        remainder = ""
    if u.audit and not re.search(r"\d", remainder):
        remainder = ""                            # "כבר כתבתי לך" carries nothing to save
    u.remainder = remainder
    return u
