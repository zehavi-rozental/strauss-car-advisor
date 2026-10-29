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
from typing import Iterable, Optional

from app.modules.rules.public import FAMILY_GROUP, CatalogCar, Faq, FaqEntry, RulesSnapshot, matched_name, related_cars, words

from .fallback import _RANK_RE, named_cars
from .parsing import _NO_COPAY_RE, COMPANY_CAR, match_occupation, no_car_span
from .stage import NO_CAR_OR_ZERO, ConversationState, Stage  # noqa: F401  (NO_CAR_OR_ZERO: re-exported)

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
_AUDIT_CUES = ("כבר כתבתי", "כבר עניתי", "כבר אמרתי", "כבר רשמתי", "כבר נתתי", "טעית", "טעיתם", "רשמת לא נכון",
               "כתבתי לך", "אמרתי לך", "עניתי לך", "כתבתי כבר", "אמרתי כבר")
# "יש לי רכב חברה" without a model: a fact (there is a car), kept so the bot never asks "אם אין רכב" again
_HAS_CAR_RE = re.compile(rf"(?<![א-ת])(?:יש\s+לי|קיבלתי|מקבל(?:ת)?|נוסע(?:ת)?\s+עם)\s+(?:כבר\s+)?{COMPANY_CAR}")
_FILLER = {"ו", "תודה", "שלום", "היי", "אוקיי", "אוקי", "ok", "טוב", "בסדר", "רגע", "אז", "נו", "אה", "אמממ",
           "יופי", "מעולה", "סבבה"}
# clauses: after "?" / "!" / "." / a new line, and before ", ולמה …" / ", ומה …" (data, then a question)
# a new clause also starts at "…, ומה…" and at "… ומה…" / "… ומי…" (a value, then an unrelated question)
_CLAUSE_RE = re.compile(r"(?<=\?)|\n|(?<=[!])\s+|(?<=\.)\s+(?=\D)|,\s*(?=ו?(?:למה|מה|האם|איך|כמה|איפה)\s)"
                        r"|\s+(?=ו(?:למה|מה|מי|האם|איך|איפה|מתי)\s)")

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
    rank: bool = False                                      # asks to rank / recommend ("הכי משתלם", "תמליץ", "דרג")
    complaint: bool = False                                 # "אתה בוט שעונה לא לפי השאלה"
    all_at_once: bool = False                               # "תן לי את כל השאלות בפעם אחת": about the conversation
    audit: bool = False                                     # "כבר כתבתי לך": say what is saved and missing
    has_car: bool = False                                   # "יש לי רכב חברה": there is a car (model unknown)
    map_all: bool = False                                   # 360°: "השווה את כל האפשרויות שלי"
    remainder: str = ""                                     # the message without questions (read by the parser)
    catalog: list[str] = field(default_factory=list)        # answers from the company catalog ("יש לכם קורולה?")
    clarify: bool = False                                   # a question whose intent is truly unclear: say it again
    # results on screen and the question is about them ("למה ההערכה זמנית?", "זה סופי?"): answered from them (C5)
    about_results: bool = False
    result_focus: list[str] = field(default_factory=list)  # the options the question names ("ולמה דווקא אטו?")

    @property
    def question_only(self) -> bool:
        """Nothing to save: only questions, a request for help, or a redirect."""
        return not meaningful(self.remainder)

    @property
    def anything(self) -> bool:
        return bool(self.audit or self.all_at_once or self.faq_ids or self.catalog or self.unanswered or self.presence or self.help or self.redirect
                    or self.ambiguity or self.compare or self.complaint or self.clarify or self.about_results)


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


# Hebrew number words that state an amount ("ילד אחד", "שני ילדים"): then it is a count, not bare presence
_COUNT_WORDS = {"אחד", "אחת", "שני", "שתי", "שניים", "שתיים", "שלושה", "שלוש", "ארבעה", "ארבע",
                "חמישה", "חמש", "שישה", "שש", "שבעה", "שבע", "שמונה", "תשעה", "תשע", "עשרה", "עשר"}


def _presence(clause: str) -> list[str]:
    if any(ch.isdigit() for ch in clause):
        return []
    ws = words(clause)
    if any(w in _NEGATIONS for w in ws) or not any(w in _PRESENCE_CUES for w in ws):
        return []
    if any(w in _COUNT_WORDS for w in ws):
        return []                               # an amount in words ("ילד אחד") — a count, read by the parser
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


def _spelling(text: str) -> str:
    """Hebrew spelling variants read alike: "האויר" / "האוויר", "ביטוח" / "בייטוח" (double vav or yod)."""
    return text.replace("וו", "ו").replace("יי", "י")


def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    low = _spelling(text.lower())
    ws = [_spelling(w) for w in words(text)]

    def exact(word: str) -> bool:           # the word itself, or with one prefix letter ("האוויר"); no typos here
        word = _spelling(word)
        return any(w == word or (len(w) > 1 and w[0] in _PREFIX and w[1:] == word) for w in ws)

    return any(_spelling(p.lower()) in low or all(exact(a) for a in words(p)) for p in patterns)


# Hebrew endings a listed word may take ("רכבים", "מרוויחה", "חשמלית"); "ת" alone only after a long word, so
# "רכב" never reads "רכבת"
_SUFFIXES = ("", "ים", "ות", "י", "ך", "ו", "ה", "נו", "כם", "כן", "ם", "ן", "יה", "יו", "ית", "יות", "יים")


def _term_hit(term: str, word: str, starts: bool = True) -> bool:
    """A listed word in the text: itself or with up to two prefix letters ("ברכב", "מהתלוש"); with `starts`, also
    with a Hebrew ending ("רכבים", "פנסיונית"). "מס" is never "מספר", "רכב" never "רכבת", "שיר" never "שירות"."""
    stems = [word]
    for _ in range(2):
        if len(stems[-1]) > 2 and stems[-1][0] in _PREFIX:
            stems.append(stems[-1][1:])
    if not starts:
        return term in stems
    return any(s == term or (len(term) >= 3 and s.startswith(term)
                             and (s[len(term):] in _SUFFIXES or (len(term) >= 4 and s[len(term):] == "ת")))
               for s in stems)


def says_phrase(text: str, phrases: Iterable[str]) -> bool:
    """A phrase said as whole, consecutive words; the first one may carry a prefix letter ("ולאט"). Never a part
    of another word: "לאטו 3" is a catalog car, not "לאט" (QA 22/10)."""
    ws = [_spelling(w) for w in words(text)]
    for phrase in phrases:
        ps = [_spelling(p) for p in words(phrase)]
        for i in range(len(ws) - len(ps) + 1):
            first, rest = ws[i], ws[i + 1:i + len(ps)]
            if ps and (first == ps[0] or (len(first) > 1 and first[0] in _PREFIX and first[1:] == ps[0])) \
                    and rest == ps[1:]:
                return True
    return False


def _names_any(terms: Iterable[str], ws: list[str], starts: bool = True) -> bool:
    """A listed term in the words. A phrase is its words in order, at most one other word between two of them
    ("רכב של החברה" is "רכב החברה"); never words from different places in the message: "כמה עולה לחברה ביטוח…
    ומגיע לי החזר" is not "עולה לי" (live QA 22/10: an arrangement question was read as a net request)."""
    for term in terms:
        parts = [_spelling(p) for p in words(term)]
        if not parts:
            continue
        for i, w in enumerate(ws):
            if not _term_hit(parts[0], w, starts):
                continue
            pos = i
            for p in parts[1:]:
                pos = next((k for k in range(pos + 1, min(pos + 3, len(ws))) if _term_hit(p, ws[k], starts)), -1)
                if pos < 0:
                    break
            if pos >= 0:
                return True
    return False


def _generic(faq: Faq, term: str) -> bool:
    """A word any car, tax or pay question has ("רכב", "מס", "שכר"): not a sign of this service alone. The word
    itself only: "הרכב" (the car we are talking about) is a reference, "רכב" (any car) is not."""
    ws = words(term)
    return len(ws) == 1 and _spelling(ws[0]) in {_spelling(t) for t in (*faq.generic_domain_terms,
                                                                         *faq.reference_terms)}


def _topic_hit(faq: Faq, clause: str, *, phrases_only: bool = False) -> bool:
    """An FAQ topic of the service: not a generic word, and not a topic about the conversation itself ("נציג",
    "פרטיות": `meta_entries`), which never makes a question one for the representative (P0-A, 22/10)."""
    return any(_alias_hits(clause, a) for e in faq.entries if e.id not in faq.meta_entries
               for a in (*e.aliases, e.term)
               if not _generic(faq, a) and not (phrases_only and len(words(a)) < 2))


def _strong(snapshot: RulesSnapshot, clause: str, ws: list[str]) -> bool:
    """Plainly about this service: a specific domain word, a catalog car or brand, a job title. A generic word
    alone ("רכב", "מס", "שכר": any car, tax or pay) is not enough (P0-A, 22/10)."""
    faq = snapshot.faq
    specific = [t for t in faq.domain_terms if not _generic(faq, t)]
    return (_names_any(specific, ws) or bool(named_cars(clause, snapshot.catalog))
            or any(_contains_any(clause, names) for names in snapshot.extensions.manufacturer_brands.values())
            or bool(match_occupation(snapshot.occupations.keys(), clause)))


def _anchored(snapshot: RulesSnapshot, clause: str, ws: list[str]) -> bool:
    """About this company's arrangement whatever else the question names: a company phrase ("רכב צמוד", "רכב
    החברה"), a catalog car, a job title from the table, an FAQ topic in more than one word ("שווי שימוש")."""
    return (_names_any(snapshot.faq.company_anchor_terms, ws, starts=False)
            or bool(named_cars(clause, snapshot.catalog))
            or bool(match_occupation(snapshot.occupations.keys(), clause))
            or _topic_hit(snapshot.faq, clause, phrases_only=True))


def off_topic(snapshot: RulesSnapshot, clause: str) -> bool:
    """Plainly not about this service (the off-topic phrases and words, exact words only), unless it also names
    the service ("מה מזג האויר?" yes; "מי משלם על הדלק בחופשה?" no)."""
    ws = [_spelling(w) for w in words(clause)]
    faq = snapshot.faq
    return (_names_any((*faq.off_topic_patterns, *faq.off_topic_terms), ws, starts=False)    # "שיר" is not "שירות"
            and not _strong(snapshot, clause, ws))


def question_scope(snapshot: RulesSnapshot, clause: str) -> str:
    """What to do with a question nothing else answered (QA 22/10, layered; never a guess from a number):
    "net" — about the employee's own pay / net: the calculation answers it, never the representative;
    "org" — a concrete question about the cars or the arrangements: the representative may answer it;
    "follow" — a follow-up to what the bot just said ("ומה זה?", "למה?"): explained, never the representative;
    "unclear" — no signal at all: one short request to say it again; "off" — plainly not about the service, or
    about another domain ("קניית דירה", "יד שנייה", "להשכרה": `foreign_context_terms`) with nothing that ties it
    to the company's arrangement (P0-A, 22/10)."""
    faq = snapshot.faq
    ws = [_spelling(w) for w in words(clause)]
    if off_topic(snapshot, clause):
        return "off"
    if _names_any(faq.foreign_context_terms, ws, starts=False) and not _anchored(snapshot, clause, ws):
        return "off"
    if _names_any(faq.net_intent_terms, ws):
        return "net"
    if _about_own_data(snapshot, clause, ws):
        return "self"
    if _strong(snapshot, clause, ws) or _topic_hit(faq, clause) or _anchored(snapshot, clause, ws):
        return "org"
    if _names_any(faq.context_markers, ws, starts=False):
        return "follow"
    if _names_any(faq.reference_terms, ws, starts=False):
        return "org"                                       # "מה המס על הרכב?": the car being discussed
    return "unclear"


def _about_own_data(snapshot: RulesSnapshot, clause: str, ws: list[str]) -> bool:
    """The question names only the employee's own data ("חייבים לכתוב עמלות?", "למה אתה שואל על ילדים?"): it is about
    why the bot asks, answered here; the representative never knows (or sees) the employee's pay (rule A, 22/10)."""
    faq = snapshot.faq
    if not faq.employee_data_terms or not _names_any(faq.employee_data_terms, ws):
        return False
    own = {_spelling(t) for t in faq.employee_data_terms}
    others = [t for t in faq.domain_terms if not _generic(faq, t) and _spelling(t) not in own]
    return not (_names_any(others, ws) or named_cars(clause, snapshot.catalog)
                or any(_contains_any(clause, names) for names in snapshot.extensions.manufacturer_brands.values())
                or _names_any(faq.company_anchor_terms, ws, starts=False)
                or match_occupation(snapshot.occupations.keys(), clause))


_DEFINITION_STARTS = ("מה זה", "מה זו", "מהו", "מהי", "מה הכוונה", "מה פירוש", "מה המשמעות")


def _about_results(snapshot: RulesSnapshot, state: ConversationState, clause: str) -> list[str]:
    """With results on screen: the options the question is about — the ones it names, or all of them when it names
    what a result is made of ("הערכה", "ממתינה לאישור", "השתתפות"); [] when it is about something else."""
    ws = [_spelling(w) for w in words(clause)]
    named = {c.catalog_code for c in named_cars(clause, snapshot.catalog)}
    focus = [r.option_id for r in state.results if r.option_id in named]
    if focus:
        return focus
    if snapshot.faq.result_terms and _names_any(snapshot.faq.result_terms, ws):
        return [r.option_id for r in state.results]
    return []


def in_scope(snapshot: RulesSnapshot, text: str) -> bool:
    """Not plainly off topic and not unclear: the service handles it (answers, calculates, explains or asks the
    representative)."""
    return question_scope(snapshot, text) not in ("off", "unclear")


def rep_scope_ok(snapshot: RulesSnapshot, text: str) -> bool:
    """May this question go to the transport representative? Checked in code at every step (the offer, the
    rewritten question, the send after "לשלוח"), whatever the model proposed: not manipulative, and a concrete
    question about the company's cars or arrangements (P0-A, 22/10)."""
    return not _contains_any(text, snapshot.faq.injection_patterns) and question_scope(snapshot, text) == "org"


def rep_scope_ok_in_context(snapshot: RulesSnapshot, text: str, *, has_car_context: bool) -> bool:
    """Like rep_scope_ok, but for the employee-initiated rep-question drawer, where the conversation is already
    about specific cars (a current car and/or options on screen). A short natural question asked in that context
    ("אפשר לקבל אותו עם וו גרירה?", "יש גרסה בצבע אחר?") is about the service even when its wording carries no
    standalone topic word, so with a car in context we accept any scope EXCEPT the ones that must never go to the
    representative: injection/off-topic (manipulative or another domain), "net"/"self" (the employee's own pay —
    the calculation answers those, the rep never sees them). Without a car in context it is the strict check."""
    if _contains_any(text, snapshot.faq.injection_patterns):
        return False
    scope = question_scope(snapshot, text)
    if scope == "org":
        return True
    if not has_car_context:
        return False
    # a car is on screen: accept a contextual question, but never pay/own-data or plainly off-topic questions
    return scope not in ("off", "net", "self")


_DRIVE_HE = {"PETROL": "בנזין", "HYBRID": "היברידי", "PLUG_IN": "פלאג-אין", "ELECTRIC": "חשמלי"}
# "אפשר לעבור לקורולה?": a wish to check a car, not a question for the representative: the catalog facts, and
# the comparison itself comes once the details are collected
_SWITCH_CUES = ("לעבור ל", "להחליף ל", "לבדוק")
# a question about whether a car exists / its group or drive (not colour, price or specs: those go to the rep)
_CATALOG_CUES = ("יש לכם", "יש אצלכם", "בקטלוג", "קיים", "קיימת", "זמין", "זמינה", "איזו קבוצה", "באיזו קבוצה",
                 "איזה הנעה", "איזו הנעה", "חשמלי או", "היברידי או")
NOT_IN_CATALOG = "לא מצאתי את הדגם הזה בקטלוג הרכבים הזמין במערכת."


def catalog_answer(snapshot: RulesSnapshot, clause: str) -> Optional[str]:
    """A question about which cars exist, answered from the catalog only (model, year, group, drive, family
    track); a brand / model question with no catalog match → "not in the catalog". Never prices or specs."""
    switching = any(cue in clause for cue in _SWITCH_CUES)
    if not (switching or any(cue in clause for cue in _CATALOG_CUES)):
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
        if switching:
            return ("בקטלוג יש: " + "; ".join(parts) + ". נחזור לזה אחרי שנסיים לאסוף את הפרטים, "
                    "ואז תבחרו אילו רכבים להשוות.")
        return "כן, בקטלוג יש: " + "; ".join(parts) + "."
    # "יש לכם פולקסווגן גולף?": a car or brand question with no catalog match
    if "דגם" in clause or "רכב" in clause or "בקטלוג" in clause or re.search(r"[A-Za-z]{2,}", clause):
        return NOT_IN_CATALOG
    return None


def understand(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Understanding:
    faq = snapshot.faq
    # "טעית"/"טעיתם" (you got it wrong) is an audit cue; "טעיתי" (I made a mistake) is a first-person
    # correction, never an audit — it must not be swallowed as one.
    audit = any(cue in text for cue in _AUDIT_CUES) and "טעיתי" not in text
    u = Understanding(complaint=not audit and any(cue in text for cue in _COMPLAINT_CUES), audit=audit,
                      has_car=bool(_HAS_CAR_RE.search(text)) and not no_car_span(text),
                      map_all=any(cue in text for cue in snapshot.extensions.option_map_cues))
    if _contains_any(text, faq.injection_patterns) or (
            off_topic(snapshot, text) and not any(ch.isdigit() for ch in text)):
        u.redirect = True
        return u              # never act on a manipulative or off-topic message (one with values goes on below:
                              # its off-topic question is redirected by in_scope, its values are read)
    if _is_help(text):
        u.help = True
        return u
    kept: list[str] = []
    faq_clauses: list[tuple[str, str]] = []      # (entry, the clause it answered)
    net_question = False
    for clause in _CLAUSE_RE.split(text):
        if clause is None or not clause.strip():
            continue
        if any(cue in clause for cue in snapshot.extensions.all_at_once_cues):
            u.all_at_once = True                  # about the conversation itself: never the representative
            continue
        short = [_spelling(w) for w in words(clause)]
        if "?" in clause and len(short) <= 3 and _names_any(faq.context_markers, short, starts=False) \
                and not any(ch.isdigit() for ch in clause) and not off_topic(snapshot, clause) \
                and match_faq(faq, clause) is None and not _strong(snapshot, clause, short):   # "מה זה השתתפות?": FAQ
            u.help = True                     # "ולמה?", "כלומר?", "והשני?": a follow-up to the bot's last message
            continue
        if _is_question(clause) and not _RANK_RE.search(clause):     # "מה הכי משתלם לי?" is a request
            if off_topic(snapshot, clause):
                u.redirect = True     # plainly not about this service: one redirect line, never the FAQ or the rep
                continue
            entry = match_faq(faq, clause)
            catalog = catalog_answer(snapshot, clause) if entry is None else None
            if entry is None and _is_help(clause):
                u.help = True
            elif catalog:
                u.catalog.append(catalog)
            elif entry is None and (u.audit or u.map_all):    # a request ("מה כל האפשרויות שלי?"), not for the rep
                pass          # "כתבתי לך שיש לי רכב, למה אתה שואל שוב?": rhetorical; answered from the state
            elif entry is None:
                scope = question_scope(snapshot, clause)
                if scope == "net":
                    net_question = True               # "אני מרוויחה יותר?": the calculation answers it
                elif scope == "self":
                    if "why_ask" not in u.faq_ids and snapshot.faq.get("why_ask") is not None:
                        u.faq_ids.append("why_ask")   # about the employee's own data: why we ask, never the rep
                elif scope == "org":
                    u.unanswered.append(clause.strip())   # a concrete question: the representative may answer
                elif scope == "follow":
                    u.help = True                     # a follow-up to the bot's last message: explained again
                elif scope == "off":
                    u.redirect = True                 # another domain ("קניית דירה"): the boundary line, never the rep
                else:
                    u.clarify = True                  # truly unclear: asked to say it again, never the rep
            elif entry not in u.faq_ids:
                u.faq_ids.append(entry)
                faq_clauses.append((entry, clause))
            continue
        kept.append(clause)
    if state.results and state.stage == Stage.PRESENTED:
        # results on screen: a follow-up, an unclear question or one about what a result is made of is about them
        # (answered from them, C5), never "choose cars" again and never the representative
        about = [q for q in u.unanswered if _about_results(snapshot, state, q)]
        # a question about the amount on screen ("מאיפה הסכום של ההשתתפות?", "איך חישבת?") is answered from the
        # result, not with the term's general definition; "מה זה השתתפות?" still gets the definition
        moved = [(e, c) for e, c in faq_clauses if _about_results(snapshot, state, c)
                 and not c.strip().startswith(_DEFINITION_STARTS)]
        if moved:
            gone = {e for e, _ in moved}
            u.faq_ids = [e for e in u.faq_ids if e not in gone]
            about += [c for _, c in moved]
        if about or u.help or u.clarify:
            u.about_results = True
            u.result_focus = [o for q in about for o in _about_results(snapshot, state, q)
                              if o != "" and len(_about_results(snapshot, state, q)) < len(state.results)]
            u.unanswered = [q for q in u.unanswered if q not in about]
            u.help = u.clarify = False
    remainder = "\n".join(c.strip() for c in kept).strip()      # clause boundaries stay (a first-line value)
    u.compare = _asks_to_compare(remainder) or u.map_all or net_question
    # a ranking / recommendation request ("הכי משתלם", "תמליץ", "דרג") is a request to rank ALL eligible
    # cars — distinct from a plain "תשווה ביניהם" that asks to compare a chosen set
    u.rank = bool(_RANK_RE.search(text))
    if u.rank:
        u.compare = True
    for part in re.split(r"[,;]|\s+ו(?=אין|יש|גם)", remainder):
        for f in _presence(part):
            if f not in u.presence:
                u.presence.append(f)
    if u.presence:
        # the presence clauses carry nothing else to save
        remainder = ", ".join(p for p in re.split(r"[,;]", remainder) if not _presence(p)).strip()
    if (_NO_COPAY_RE.search(remainder) and state.current.source is None and not state.current.query
            and not state.current.has_car and not u.has_car
            and not named_cars(remainder, snapshot.catalog) and not no_car_span(remainder)):
        u.ambiguity = NO_CAR_OR_ZERO
        remainder = ""
    if u.audit and not re.search(r"\d", remainder) and "קבוצ" not in remainder:
        remainder = ""                            # "כבר כתבתי לך" carries nothing to save (but a group
        #                                           correction like "טעיתי, אני בקבוצה ד" does)
    u.remainder = remainder
    return u
