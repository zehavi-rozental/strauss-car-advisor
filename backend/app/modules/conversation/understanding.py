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
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Iterable, Optional

from app.modules.rules.public import FAMILY_GROUP, CatalogCar, Faq, FaqEntry, RulesSnapshot, matched_name, related_cars, words

from . import actions, data_facts
from .fallback import _RANK_RE, named_cars
from .grounding import CONTRIBUTION_FIELDS, bare_zero_answer, contributions_zero_said, has_zero_cue, word_numbers
from .parsing import _NO_COPAY_RE, COMPANY_CAR, match_drive, match_group, match_occupation, no_car_span
from .stage import NO_CAR_OR_ZERO, ConversationState, Stage, results_stale  # noqa: F401  (NO_CAR_OR_ZERO: re-exported)

_QUESTION_WORDS = ("מה", "מהו", "מהי", "מהם", "למה", "איך", "איפה", "האם", "מתי", "מי", "מאיפה", "ממה", "כמה זה",
                   "מה זה", "מה ההבדל", "באיזה", "לאן")
# the whole message asks to explain the current question again
_HELP = {("מה",), ("לא", "הבנתי"), ("לא", "הבנתי", "מה"), ("עזרה",), ("הסבר",), ("תסביר",), ("לא", "ברור"),
         ("מה", "זאת", "אומרת"), ("מה", "הכוונה"), ("מה", "אתה", "רוצה"), ("מה", "צריך"), ("help",),
         ("מה", "לכתוב"), ("לא", "הבנתי", "את", "השאלה"), ("מה", "השאלה"), ("אני", "לא", "מבין"),
         ("אני", "לא", "מבינה")}
_QUESTION_CUES = ("האם", "רוצה לדעת", "רוצים לדעת", "אשמח לדעת", "תגיד לי", "תגידו לי", "תסביר", "יש אפשרות",
                  "אפשר לקבל", "מעניין אותי לדעת")
# "לא הבנת" is matched as whole words (faq.feedback_cues): as a substring it also matched "לא הבנתי" (help)
_COMPLAINT_CUES = ("לא עונה", "לא ענית", "לא לפי השאלה", "לא עניתם", "לא עזרת", "בוט", "שאלתי משהו אחר",
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
_ELIGIBLE_REFERENTS = {"הם", "הן", "אותם", "אותן", "מהם", "מהן", "מתוכם", "מתוכן"}


@dataclass
class Understanding:
    faq_ids: list[str] = field(default_factory=list)       # questions answered from faq.yaml, in order
    unanswered: list[str] = field(default_factory=list)    # questions no FAQ entry covers
    missing_data: bool = False                              # a question about a value the calculation still needs
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
    # a term explained this turn (an FAQ id): the next message's "זה" / "לא הבנתי" / "איפה זה בתלוש?" is about it
    topic: Optional[str] = None
    # answers about the term a question points back to ("איפה זה בתלוש?"): (line, the entry's term)
    topic_lines: list[tuple[str, str]] = field(default_factory=list)
    # the cars open to the employee asked about (15/11): "" all of them, or a drive ("ELECTRIC"); answered by code after
    # this turn's saves (a group corrected in the same message counts), never sent to the representative
    eligible: Optional[str] = None
    # the family-track note goes with this answer: said once, while the number of children is unknown (16/11)
    family_note: bool = False
    # "אני רוצה לדבר עם נציג", "היית צריך לשאול את הנציג": the offer to ask, at once (29/10)
    rep_request: bool = False
    # "אני רוצה להעלות תלוש" (1/11): the uploader is opened at once, at any stage
    payslip_request: bool = False
    # "תציג לי אפשרויות", "תראה לי רשימה" (1/11): the cars, from the catalog, at the question about today's car or the
    # alternatives (faq.show_list_verbs + show_list_objects); the clause is never read as a car name
    show_list: bool = False
    # small talk and nothing else ("מה נשמע?", "בוקר טוב", "תודה"): its kind in faq.social_cues (30/10)
    social: Optional[str] = None
    # 24/11: the message asks nothing of the service: "small_talk" (thanks, how are you, "are you happy") or "off_topic"
    # (weather, news, an attempt to change the rules). Answered by code: a fixed line and the follow-up for the stage
    aside: Optional[str] = None
    # nothing was answered, but nothing was missed either (small talk, "שאלה אחת כל פעם", "יותר קצר"): the open
    # question comes back as it was, picked up where it stopped, never as "still missing" (30/10)
    resume: bool = False
    # "אני רוצה לתקן נתון", "השכר שהכנסתי לא נכון" (1/11): a correction of the employee's own data, with no value yet.
    # None: not one; [] no field named; else the save_profile arguments it names ("car" for today's car)
    correction: Optional[list[str]] = None
    # "אין הפרשות", "לא יורד לי כלום" (1/11): nothing deducted, said for both contributions at once; the fields it
    # holds at 0 for one confirmation (the missing ones, else both: a correction)
    zero_contributions: list[str] = field(default_factory=list)
    # an action on the screen this turn points to (actions.py, 19/11): a question or a follow-up about it, or a message
    # about its topic. Its help line is in topic_lines; the reply carries the action itself (never only words)
    action: Optional[str] = None

    @property
    def question_only(self) -> bool:
        """Nothing to save: only questions, a request for help, or a redirect."""
        return not meaningful(self.remainder)

    @property
    def anything(self) -> bool:
        return bool(self.audit or self.all_at_once or self.faq_ids or self.topic_lines or self.catalog or self.unanswered
                    or self.eligible is not None
                    or self.presence or self.help or self.redirect
                    or self.ambiguity or self.compare or self.complaint or self.clarify or self.about_results
                    or self.rep_request or self.social or self.correction is not None
                    or bool(self.zero_contributions) or self.action is not None)


# the small talk answered by the fixed line (24/11); a greeting opens the conversation and keeps its own reply
SMALL_TALK = ("how_are_you", "thanks", "about_bot")
# words that may stand next to small talk without making it more ("מה נשמע אצלכם היום?")
_SOCIAL_FILLER = {"אצלך", "אצלכם", "היום", "לך", "לכם", "רב", "מאוד", "ממש", "שוב", "באמת", "גם", "אחי", "חבר"}


def social_kind(faq: Faq, text: str) -> Optional[str]:
    """The kind of small talk when the message is nothing else (30/10): every word is part of a listed phrase
    (faq.social_cues, a prefix letter allowed on its first word) or a filler word. "היי, אני בקבוצה ג" carries a
    value, so it is not small talk; "מה נשמע?" is. The kind asked about ("how are you") wins over a greeting."""
    ws = [_spelling(w) for w in words(text)]
    if not ws or any(ch.isdigit() for ch in text):
        return None
    covered = [False] * len(ws)
    found: list[str] = []
    for kind, phrases in faq.social_cues.items():
        for phrase in sorted(phrases, key=lambda p: -len(words(p))):
            ps = [_spelling(p) for p in words(phrase)]
            for i in range(len(ws) - len(ps) + 1):
                first = ws[i]
                if (first == ps[0] or (len(first) > 1 and first[0] in _PREFIX and first[1:] == ps[0])) \
                        and ws[i + 1:i + len(ps)] == ps[1:]:
                    covered[i:i + len(ps)] = [True] * len(ps)
                    found.append(kind)
    if not found or not all(c or w in _FILLER or w in _SOCIAL_FILLER for c, w in zip(covered, ws)):
        return None
    return next((k for k in ("how_are_you", "thanks", "greeting") if k in found), found[0])


def without_greeting(faq: Faq, text: str) -> str:
    """The message without the small talk it opens with ("היי, אני בקבוצה ג" -> "אני בקבוצה ג"): the parser reads the
    rest; the greeting itself carries no value (30/10)."""
    phrases = sorted((p for ps in faq.social_cues.values() for p in ps), key=len, reverse=True)
    if not phrases:
        return text
    lead = re.compile(r"^\s*(?:(?:" + "|".join(map(re.escape, phrases)) + r")(?![א-ת\w])[\s,.!?:;-]*)+")
    return lead.sub("", text, count=1)


def gives_feedback(faq: Faq, text: str) -> bool:
    """About the bot's previous reply ("מה הקשר", "זה לא תשובה", "לא קשור"): whole words, so "לא הבנתי" (the
    employee asks for help) is never "לא הבנת" (feedback)."""
    return bool(faq.feedback_cues) and says_phrase(text, faq.feedback_cues)


def meaningful(text: str) -> bool:
    return any(w not in _FILLER for w in words(text))


def _word_match(alias_word: str, word: str) -> bool:
    if word == alias_word or (len(word) > 1 and word[0] in _PREFIX and word[1:] == alias_word):
        return True
    if len(alias_word) >= 3 and len(word) >= 3:      # typos: "סכם" ~ "סכום", "נקודת" ~ "נקודות"
        return SequenceMatcher(None, alias_word, word.lstrip(_PREFIX) or word).ratio() >= 0.8
    return False


def _alias_hits(text: str, alias: str, typos: bool = False) -> bool:
    """Every word of the alias is in the text. Typos are tolerated only in a phrase ("סכם מדויק"); a single
    word must be the word itself ("אפשרות" is not "אפשרויות"), unless the term was asked to be explained (`typos`:
    "מה זה הפרשוצ?"), where a long word may differ by one letter."""
    ws, aw = words(text), words(alias)
    if len(aw) == 1:
        a = aw[0]
        if any(w == a or (len(w) > 1 and w[0] in _PREFIX and w[1:] == a) for w in ws):
            return True
        return typos and len(a) >= 5 and any(
            SequenceMatcher(None, _spelling(a), _spelling(w)).ratio() >= 0.8 for w in ws if len(w) >= 5)
    return all(any(_word_match(a, w) for w in ws) for a in aw)


def entry_answers(entry: FaqEntry, text: str) -> bool:
    """Relevance check for an FAQ id the LLM proposed: the question names the entry's topic (one of its
    aliases or its term). Otherwise the entry does not answer it — no FAQ answer, only the honest line."""
    return any(_alias_hits(text, a) for a in (*entry.aliases, entry.term))


def match_faq(faq: Faq, text: str, explain: bool = False) -> Optional[str]:
    """The FAQ entry a question is about: the entry with the longest alias it contains (typos allowed). A request to
    explain (`explain`: "מה ההבדל בין ברוטו לנטו?") names its term, so a one-word alias counts in a long one too."""
    best: tuple[int, int, str] | None = None
    long_question = len(words(text)) > 4 and not explain
    for entry in faq.entries:
        for alias in entry.aliases:
            # one word ("שכר") names the topic only in a short question; a long one needs a phrase
            if long_question and len(words(alias)) == 1:
                continue
            if _alias_hits(text, alias, typos=explain):
                score = (len(words(alias)), len(alias), entry.id)
                if best is None or score[:2] > best[:2]:
                    best = score
    return best[2] if best else None


def asks_explanation(faq: Faq, clause: str) -> bool:
    """A request to explain a term ("מה זה אומר הפרשות?", "תסביר לי שווי שימוש", "מה ההבדל בין ברוטו לנטו?", 10/11):
    answered first from what it names, never read as "why do you ask", a net request or an answer."""
    return bool(faq.explain_cues) and says_phrase(clause, faq.explain_cues)


def without_requery(faq: Faq, text: str) -> str:
    """"שאלתי מה זה הפרשות": the question said again, without its opening words (10/11), so it is read as the question
    it is, never as an answer to the question on screen. The text as it was when nothing else follows a cue."""
    ws = words(text)
    for cue in sorted(faq.requery_cues, key=lambda c: -len(words(c))):
        cw = words(cue)
        if [_spelling(w) for w in ws[:len(cw)]] == [_spelling(w) for w in cw] and len(ws) > len(cw):
            rest = text.strip()
            for w in cw:                       # drop the cue's words with what separates them (",", ":")
                rest = rest[rest.index(w) + len(w):].lstrip(" ,:-–")
            if _is_question(rest) or asks_explanation(faq, rest):
                return rest
    return text


def without_restatement(snapshot: RulesSnapshot, text: str) -> str:
    """"כתבתי שכלום", "אמרתי שאין לי": an answer said again, without its opening words (19/11), the same way a question
    said again is read (without_requery). The "ש" that joins it is dropped only when what follows is then an answer
    the conversation reads (a zero, a number, a car, a group) — "כתבתי שכר" keeps its word."""
    faq = snapshot.faq
    ws = words(text)
    for cue in sorted(faq.restate_cues, key=lambda c: -len(words(c))):
        cw = words(cue)
        if [_spelling(w) for w in ws[:len(cw)]] != [_spelling(w) for w in cw] or len(ws) <= len(cw):
            continue
        rest = text.strip()
        for w in cw:
            rest = rest[rest.index(w) + len(w):].lstrip(" ,:-–")
        ext = snapshot.extensions

        def reads(t: str) -> bool:           # an answer the conversation reads (a value, a zero, a bare "no")
            return (answer_like(snapshot, t) or has_zero_cue(t, ext.zero_cues)
                    or bare_zero_answer(t, ext.bare_zero_negatives, ext.bare_zero_words))
        if rest.startswith("ש") and len(rest) > 2 and not reads(rest) and reads(rest[1:]):
            return rest[1:]                  # "שכלום" -> "כלום"; "שכר 18,500" reads as it is and keeps its word
        return rest
    return text


_BARE_NO_RE = re.compile(r"\s*(?:אין|לא|אין\s+לי)\s*[.!]*\s*")


def _explain_topic_again(snapshot: RulesSnapshot, state: ConversationState, u: "Understanding") -> bool:
    """"לא הבנתי" / "מה זה אומר?" right after a term was explained: the term in simpler words (the question on screen
    follows), never the question alone as if nothing was explained. False when the last reply explained nothing."""
    entry = snapshot.faq.get(state.last_topic) if state.last_topic else None
    if entry is None or not entry.simple:
        return False
    u.topic_lines.append((f"במילים פשוטות: {entry.simple}", entry.term))
    u.topic = entry.id
    return True


# the FAQ entry about the cars on offer: the topic kept after an answer about the open cars, so "ומה מהם חשמליים?" follows
ELIGIBLE_TOPIC = "options"
# how many turns after an answer about the open cars a follow-up still refers to it (a question between included)
ELIGIBLE_CONTEXT_TURNS = 3
# a bare follow-up that may be about the cars or about the net on screen: one short question instead of a guess
ELIGIBLE_UNCLEAR = "?"


def asks_eligible(snapshot: RulesSnapshot, state: ConversationState, clause: str) -> Optional[str]:
    """The cars open to this employee asked about (15/11): "" for all of them, a drive's name for those of one drive
    ("ומה מהם חשמליים?"), None when the clause is about something else. An object word with an eligibility, count or
    list word, or a follow-up right after such an answer; never a question about one named car (the catalog's own
    facts) or a property the catalog does not hold. Needs the group: saved, or said in the same message."""
    faq = snapshot.faq
    if not faq.eligible_objects or asks_attribute(snapshot, clause) or named_cars(clause, snapshot.catalog):
        return None
    if "קטלוג" in clause:
        return None          # "אילו רכבים היברידיים יש בקטלוג?": the whole catalog, answered from it as before
    ws = [_spelling(w) for w in words(clause)]
    drive = match_drive(clause, named_only=True)   # "חוסך דלק" names no drive: the list, never petrol (15/11)
    about_list = (_names_any(faq.eligible_objects, ws, starts=False)
                  and _names_any(faq.eligible_cues, ws, starts=False))
    # a follow-up while the open-cars context lasts (16/11: not only the very next turn)
    recent = state.eligible_turns is not None
    # without a drive, only a clause made of follow-up words ("ועכשיו כמה?", "ומתוכם?"): "כמה אחוז פנסיה?" or "כמה זה
    # עולה?" ask something else
    only_follow = bool(ws) and all(_names_any(faq.eligible_follow_up, [w], starts=False) for w in ws)
    refers_to_eligible = (drive is not None and bool(state.profile.group)
                          and any(w in _ELIGIBLE_REFERENTS for w in ws))
    follow = (recent and (drive is not None or only_follow)) or refers_to_eligible
    if not (about_list or follow):
        return None
    if not about_list and drive is None and state.eligible_turns and state.results and not results_stale(state):
        # "ועכשיו כמה?" a turn or more later, with net figures on screen: the cars, or the net? asked, never guessed
        return ELIGIBLE_UNCLEAR
    return drive.value if drive is not None else ""


def refers_back(faq: Faq, clause: str) -> bool:
    """"איפה זה בתלוש?", "למה צריך את זה?": the question points back to the conversation."""
    return bool(faq.reference_words) and _names_any(faq.reference_words, [_spelling(w) for w in words(clause)],
                                                    starts=False)


_FAQ_OF_FIELD = {"base_salary": "base", "children_under_23": "children", "pension": "pension_percent"}


def asked_contributions(state: ConversationState) -> bool:
    """The last question asked the two contributions together and nothing else (19/11): read from what the question
    on screen really asked (templates.ask_fields: after a partial salary answer, one item at a time; "אילו הפרשות
    יורדות לכם: פנסיה, קרן השתלמות?"), not from the whole salary group it belongs to."""
    if state.asked_field is not None:
        return False
    from .stage import current_group
    from .templates import ask_fields               # lazy: templates' neighbours import this module
    group = current_group(state)[0]
    asked = ask_fields(state, group, list(state.last_asked)) if state.last_asked else []
    return set(asked) == set(CONTRIBUTION_FIELDS)


def referent(snapshot: RulesSnapshot, state: ConversationState) -> Optional[FaqEntry]:
    """What "זה" is about: the term the last reply explained, else the one field the last reply asked (both
    contributions asked together: "הפרשות")."""
    faq = snapshot.faq
    if state.last_topic and (entry := faq.get(state.last_topic)) is not None:
        return entry
    if asked_contributions(state):
        return faq.get("contributions")
    field_name = state.asked_field or (state.last_asked[0] if len(state.last_asked) == 1 else None)
    return faq.get(_FAQ_OF_FIELD.get(field_name, field_name)) if field_name else None


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


def says_all_words(text: str, word_sets: Iterable[Iterable[str]]) -> bool:
    """Every word of one set is in the message, in any order, each a whole word (a prefix letter allowed):
    "תשאל כל פעם שאלה אחת" has [שאלה, אחת, פעם]; "יש לי שאלה אחת" does not (30/10)."""
    ws = [_spelling(w) for w in words(text)]

    def has(word: str) -> bool:
        p = _spelling(word)
        return any(w == p or (len(w) > 1 and w[0] in _PREFIX and w[1:] == p) for w in ws)

    return any(all(has(w) for w in group) for group in word_sets if group)


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


def asks_attribute(snapshot: RulesSnapshot, clause: str) -> bool:
    """A property of a car the catalog does not hold (colour, sunroof, equipment, stock, delivery): `attribute_terms`.
    Never a model name, never answered from the catalog (29/10)."""
    return bool(snapshot.faq.attribute_terms) and _names_any(snapshot.faq.attribute_terms,
                                                             [_spelling(w) for w in words(clause)])


def _about_a_car(snapshot: RulesSnapshot, clause: str, ws: list[str]) -> bool:
    """The question is about a car: a catalog car or brand, or a car word ("רכב", "רכבים", "הרכב")."""
    faq = snapshot.faq
    car_words = [t for t in faq.generic_domain_terms if t in ("רכב", "מכונית", "אוטו")] + ["רכבים", "מכוניות"]
    return (bool(named_cars(clause, snapshot.catalog))
            or any(_contains_any(clause, names) for names in snapshot.extensions.manufacturer_brands.values())
            or _names_any((*car_words, *faq.reference_terms), ws))


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
    if asks_attribute(snapshot, clause) and _about_a_car(snapshot, clause, ws):
        return "org"                    # "יש לכם רכב אדום?": a property of the company's cars, for the representative
    if _strong(snapshot, clause, ws) or _topic_hit(faq, clause) or _anchored(snapshot, clause, ws):
        return "org"
    if _about_bot(faq, ws):
        return "bot"                    # "איך אתה יודע את כל התשובות?": about the advisor, never "explain again"
    if _names_any(faq.context_markers, ws, starts=False):
        return "follow"
    if _names_any(faq.reference_terms, ws, starts=False):
        return "org"                                       # "מה המס על הרכב?": the car being discussed
    return "unclear"


def _about_bot(faq: Faq, ws: list[str]) -> bool:
    """The question refers to the advisor itself ("אתה", "שלך", "המערכת"): a question about how it works (30/10)."""
    return bool(faq.bot_reference_terms) and _names_any(faq.bot_reference_terms, ws, starts=False)


def asks_basis(faq: Faq, text: str) -> bool:
    """A question about what the advice rests on ("על מה מבוססות ההמלצות שלך?"): it names the advisor and a basis word.
    A ranking word in it ("המלצות") never makes it a ranking request; "מה אתה ממליץ לי?" names no basis (31/10)."""
    ws = [_spelling(w) for w in words(text)]
    return _is_question(text) and _about_bot(faq, ws) and _names_any(faq.bot_basis_terms, ws, starts=False)


_YES_NO = {"כן", "לא", "אין", "יש", "בלי"}


def answer_like(snapshot: RulesSnapshot, text: str) -> bool:
    """Could this be an answer to the bot's question at all (30/10)? A number, a group letter, a catalog car or
    brand, a job title, a word of the service or of the employee's data, yes / no. When a message that the parser
    could not read has none of these, it was something else (a remark, a request), never "a failed answer"."""
    if not text.strip():
        return False
    if any(ch.isdigit() for ch in text):
        return True
    ws = [_spelling(w) for w in words(text)]
    faq = snapshot.faq
    groups = {_spelling(g) for g in snapshot.calc.group_order}
    # yes / no answer a question only when they open a short message ("לא, אין לי"); "אני לא בטוחה…" is no answer
    yes_no = bool(ws) and ws[0] in _YES_NO and len(ws) <= 4
    return (yes_no or any(w in groups for w in ws)
            or bool(named_cars(text, snapshot.catalog))
            or any(_contains_any(text, names) for names in snapshot.extensions.manufacturer_brands.values())
            or bool(match_occupation(snapshot.occupations.keys(), text))
            or _names_any((*faq.domain_terms, *faq.employee_data_terms, *_PRESENCE_WORDS), ws))


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
    if _compares(snapshot, clause) and len(focus) >= 2 and len(focus) == len(named):
        return focus     # two results on screen compared: the engine's comparison facts answer it (stage 2, 16/11)
    if asks_attribute(snapshot, clause) or _compares(snapshot, clause) or (ws and ws[0] == "מתי"):
        return []        # a colour / sunroof / stock / "when" question, or cars compared from the catalog: not results
    if focus:
        return focus
    if snapshot.faq.result_terms and _names_any(snapshot.faq.result_terms, ws):
        return [r.option_id for r in state.results]
    return []


def asks_estimate(snapshot: RulesSnapshot, text: str) -> bool:
    """A question about the estimate's status or its copay ("למה ההערכה זמנית?", "זה סופי?", "מאיפה ההשתתפות?"),
    not about why a car leads (15/11). Without estimate_terms configured: every result question, as before."""
    terms = snapshot.faq.estimate_terms
    return not terms or _names_any(terms, [_spelling(w) for w in words(text)])


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
# "רכב" followed by one of these names a kind of car, not a model: "לעבור לרכב צמוד?" is no model question (31/10)
_GENERIC_CAR_RE = re.compile(r"רכב\s+ה?(?:צמוד|חברה|אחר|חדש|חשמלי|היברידי|משפחתי|שלי|שלכם)")


def _compares(snapshot: RulesSnapshot, clause: str) -> bool:
    """Two catalog cars or more, compared ("מה ההבדל בין NIRO ל-DOLPHIN?")."""
    return (bool(snapshot.faq.comparison_terms) and len(named_cars(clause, snapshot.catalog)) >= 2
            and _names_any(snapshot.faq.comparison_terms, [_spelling(w) for w in words(clause)]))


def _car_facts(c: CatalogCar) -> str:
    return (f"{c.label}, " + ("במסלול המשפחתי" if c.group == FAMILY_GROUP else f"קבוצה {c.group}")
            + f", {_DRIVE_HE[c.drive.value]}")


def _shekel(value) -> str:
    return f"{int(round(value)):,} ₪"


# a fact the company catalog holds for each car (1/11): answered from it directly, in both modes, never by the model
_FACT_CUES = (
    (("מחיר", "מחירון", "כמה עולה", "כמה עולים", "עלות הרכב"), lambda c: f"מחיר המחירון של {c.label}: {_shekel(c.list_price)}."),
    (("שווי השימוש", "שווי שימוש", "שווי"), lambda c: f"שווי השימוש של {c.label} לפי רשות המסים: {_shekel(c.use_value)} בחודש."),
    (("השתתפות", "כמה משלמים", "כמה אשלם"), lambda c: (
        f"ההשתתפות של העובד ב-{c.label} לפי הקטלוג: {_shekel(c.default_copay)} בחודש. זה סכום כללי; הסכום שלכם "
        "יכול להיות שונה." if c.default_copay is not None else None)),
    (("יצרן", "מי מייצר", "של איזו חברה", "איזו חברה"), lambda c: f"היצרן של {c.label}: {c.manufacturer}."),
    (("שנת", "איזו שנה", "מאיזו שנה", "שנתון"), lambda c: f"{c.label} בקטלוג הוא משנת {c.reg_year}."),
    (("קבוצה", "בקבוצה", "קבוצת"), lambda c: f"{c.label} בקטלוג: " + (
        "במסלול המשפחתי." if c.group == FAMILY_GROUP else f"קבוצה {c.group}.")),
    (("הנעה", "חשמלי", "חשמלית", "היברידי", "היברידית", "בנזין", "פלאג"),
     lambda c: f"{c.label} בקטלוג: {_DRIVE_HE[c.drive.value]}."),
)


def catalog_fact(snapshot: RulesSnapshot, clause: str) -> Optional[str]:
    """A fact about ONE catalog car that the catalog holds (price, use value, catalog copay, manufacturer, year, group,
    drive): the catalog's own answer. A property it does not hold (colour, sunroof, equipment) is None (the
    representative), and so is a comparison of two cars or a question with no car named."""
    if asks_attribute(snapshot, clause) or not _is_question(clause):
        return None
    cars = named_cars(clause, snapshot.catalog)
    if len(cars) != 1:
        return None
    for cues, say in _FACT_CUES:
        if any(cue in clause for cue in cues):
            return say(cars[0])
    return None


_NET_DIFF_CUES = ("נטו", "הפרש", "יותר משתלם", "משתלם יותר", "מרוויח", "מרוויחה")


def results_difference(state: ConversationState, snapshot: RulesSnapshot, clause: str) -> Optional[str]:
    """"מה ההבדל בנטו בין NIRO ל-DOLPHIN?" with both cars calculated (1/11): the engine's own figures, said in words —
    each one's change against today and the gap between them; never "which cars to compare" again. None when a car
    is not calculated, the results are out of date, or the question is not about the net."""
    if not any(cue in clause for cue in _NET_DIFF_CUES) or results_stale(state):
        return None
    cars = named_cars(clause, snapshot.catalog)
    if len(cars) < 2:
        return None
    found = []
    for car in cars[:3]:
        r = next((x for x in state.results if x.option_id == car.catalog_code), None)
        if r is None or r.result.get("status") != "OK" or r.result.get("delta_display") is None:
            return None
        found.append((car.label, Decimal(str(r.result["delta_display"])), Decimal(str(r.result["alternative"]["net_display"]))))
    def change(d):
        return f"יגדל ב-{_shekel(d)}" if d > 0 else f"יקטן ב-{_shekel(-d)}" if d < 0 else "לא ישתנה"
    lines = [f"ב-{label} הנטו שלכם {change(d)} בחודש (נטו חדש: {_shekel(net)})." for label, d, net in found]
    if len(found) == 2:
        (a, da, _), (b, db, _) = found
        if da == db:
            lines.append("ההפרש ביניהם: 0 ₪. הנטו זהה.")
        else:
            best, gap = (a, da - db) if da > db else (b, db - da)
            lines.append(f"ההפרש ביניהם: {_shekel(gap)} בחודש לטובת {best}.")
    return "\n".join(lines)


def catalog_answer(snapshot: RulesSnapshot, clause: str, on_screen: Iterable[str] = ()) -> Optional[str]:
    """A question about which cars exist, answered from the catalog only (model, year, group, drive, family
    track); a brand / model question with no catalog match → "not in the catalog". Never prices or specs.
    A property the catalog does not hold (colour, sunroof, stock) is never answered here — not even "yes, it is in
    the catalog", which reads as yes to the colour (29/10): it is a question for the representative. Two cars
    compared: the catalog facts of each, and where their effect on the net is (never a copay)."""
    if asks_attribute(snapshot, clause):
        return None
    if _compares(snapshot, clause):
        cars = named_cars(clause, snapshot.catalog)
        shown = set(on_screen)
        where = ("ההשפעה של כל אחד מהם על הנטו מופיעה בתוצאות." if all(c.catalog_code in shown for c in cars)
                 else "כדי לראות איך כל אחד מהם משפיע על הנטו, אפשר לבחור אותם להשוואה.")
        return "לפי הקטלוג: " + "; ".join(_car_facts(c) for c in cars) + ". " + where
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
    if "דגם" in clause or "רכב" in _GENERIC_CAR_RE.sub("", clause) or "בקטלוג" in clause \
            or re.search(r"[A-Za-z]{2,}", clause):
        return NOT_IN_CATALOG
    return None


# the field a correction request names (1/11), by the save_profile argument it changes ("car": today's car)
_CORRECTION_FIELDS = (("base_salary", ("שכר", "משכורת")), ("commissions", ("עמל",)), ("bonus", ("בונוס",)),
                      ("pension_percent", ("פנסי",)), ("study_fund_percent", ("השתלמות",)), ("credit_points", ("זיכוי",)),
                      ("children_under_23", ("ילד",)), ("group", ("קבוצ",)), ("current_copay", ("השתתפות",)),
                      ("car", ("רכב", "דגם")))
# a wish to change ("להחליף רכב") is not a mistake: with only the car named, it is the service (another car), never
# a correction of today's car
_CHANGE_ONLY = {"לשנות", "להחליף", "לעדכן", "שינוי", "עדכון"}
_GROUP_SAID_RE = re.compile(r"קבוצ[הת]\s*['\"]?[א-ד](?![א-ת])")
# "הקבוצה שלי היא ג" (15/11): the group's letter as a word of its own, in a message about the group, is a value
_GROUP_LETTER_RE = re.compile(r"(?<![א-ת])[א-ד]['׳\"]?(?![א-ת])")


def _group_said(text: str) -> bool:
    return bool(_GROUP_SAID_RE.search(text) or ("קבוצ" in text and _GROUP_LETTER_RE.search(text)))


def _names_stem(ws: list[str], stems: Iterable[str]) -> bool:
    """A word starts with one of the stems, after at most two prefix letters ("והשכר", "בפנסיה")."""
    return any(w[i:].startswith(s) for w in ws for s in stems for i in range(3)
               if i == 0 or all(c in _PREFIX for c in w[:i]))


def correction_request(snapshot: RulesSnapshot, text: str) -> Optional[list[str]]:
    """The employee wants to correct data they gave, and gave no new value yet (1/11): a change or mistake word
    (faq.correction_terms) next to a data word (faq.correction_objects) or a salary / car field. With a value in the
    message ("טעיתי, השכר שלי 20,000", "אין לי עמלות", "קבוצה ב", a car named) it is the correction itself, read as
    usual. Returns the fields it names ([] for none), or None."""
    faq = snapshot.faq
    if not faq.correction_terms or any(ch.isdigit() for ch in text) or any(n != 1 for n in word_numbers(text)):
        return None                       # a value in digits or in words ("תשעה עשר וחצי אלף") is the correction;
                                          # "אחד" alone is an article ("נתון אחד"), not a value
    ws = [_spelling(w) for w in words(text)]
    if not _names_any(faq.correction_terms, ws, starts=False):
        return None
    if ws[0] in ("למה", "מדוע", "איך", "כיצד"):
        return None                       # "למה ההשתתפות לא נכונה?": a question about the result, answered from it
    if (any(w in _NEGATIONS - {"לא"} for w in ws) or _group_said(text) or named_cars(text, snapshot.catalog)
            or any(_contains_any(text, names) for names in snapshot.extensions.manufacturer_brands.values())):
        return None
    fields = [arg for arg, stems in _CORRECTION_FIELDS if _names_stem(ws, stems)]
    objects = bool(faq.correction_objects) and _names_any(faq.correction_objects, ws, starts=False)
    mistake = any(_names_any((t,), ws, starts=False) for t in faq.correction_terms if t not in _CHANGE_ONLY)
    if fields == ["car"] and not (mistake or objects):
        return None                       # "אני רוצה להחליף את הרכב": another car, the service itself
    if not fields and not objects:
        return None
    return fields


def _offered_action(state: ConversationState, snapshot: RulesSnapshot, about_it: bool = False) -> Optional[str]:
    """The action the last reply offered that is still on the screen (19/11). about_it: only when the reply was about
    that action (its topic), not one that only stood next to a data question ("…, או להעלות תלוש")."""
    now = actions.available(state, snapshot)
    last = state.last_reply
    for kind in last.offered:
        if kind in now and (not about_it or last.topic == actions.TOPICS.get(kind)):
            return kind
    return None


def _choosing_cars(state: ConversationState) -> bool:
    """The conversation is about the cars to choose (the alternatives asked, or results on screen), and today's car is not
    the question (19/11): a car property said then is a request about a new car. Earlier, "the white Hyundai is mine"
    describes today's car and a wish for "something small" comes before any data: both go on as before."""
    from .stage import next_stage
    return next_stage(state) in (Stage.CHOOSE_OPTIONS, Stage.PRESENTED) or bool(state.selected)


def _carries_value(snapshot: RulesSnapshot, text: str) -> bool:
    """A value the parser could save is in the text: a number, a group letter, a catalog car or a job title (a word
    of the salary domain alone, "תלוש", is no value)."""
    groups = {_spelling(g) for g in snapshot.calc.group_order}
    return (any(ch.isdigit() for ch in text) or any(_spelling(w) in groups for w in words(text))
            or bool(named_cars(text, snapshot.catalog)) or bool(match_occupation(snapshot.occupations.keys(), text)))


def _point_to(u: Understanding, kind: str) -> None:
    """This turn is about an action on the screen: where it is and what it does (code's words), and the reply carries
    the action itself. "זה" in the next message is about it."""
    u.action = kind
    line = (actions.HELP[kind], actions.LABELS[kind])
    if line not in u.topic_lines:
        u.topic_lines.append(line)
    u.topic = actions.TOPICS.get(kind)


def understand(state: ConversationState, snapshot: RulesSnapshot, text: str) -> Understanding:
    faq = snapshot.faq
    # "טעית"/"טעיתם" (you got it wrong) is an audit cue; "טעיתי" (I made a mistake) is a first-person
    # correction, never an audit — it must not be swallowed as one.
    audit = any(cue in text for cue in _AUDIT_CUES) and "טעיתי" not in text
    u = Understanding(complaint=not audit and (any(cue in text for cue in _COMPLAINT_CUES) or gives_feedback(faq, text)),
                      audit=audit, has_car=bool(_HAS_CAR_RE.search(text)) and not no_car_span(text),
                      map_all=any(cue in text for cue in snapshot.extensions.option_map_cues))
    text = without_requery(faq, text)      # "שאלתי מה זה הפרשות": the question again, read as one (10/11)
    text = without_restatement(snapshot, text)   # "כתבתי שכלום": the answer again, read as one (19/11)
    kind = social_kind(faq, text)
    if kind is not None:
        u.social, u.remainder, u.resume = kind, "", True
        u.aside = "small_talk" if kind in SMALL_TALK else None
        return u              # small talk only: answered in a few words, then the open question (never a miss)
    if (faq.proceed_terms and _names_any(faq.proceed_terms, [_spelling(w) for w in words(text)], starts=False)
            and not answer_like(snapshot, text)):
        # "let's move on" with nothing to read in it: the open question, picked up (31/10)
        u.social, u.remainder, u.resume = "proceed", "", True
        return u
    if _contains_any(text, faq.injection_patterns) or (
            off_topic(snapshot, text) and not any(ch.isdigit() for ch in text)):
        u.redirect = True
        u.aside = None if (u.complaint or u.audit) else "off_topic"
        return u              # never act on a manipulative or off-topic message (one with values goes on below:
                              # its off-topic question is redirected by in_scope, its values are read)
    if _is_help(text):
        if (kind := _offered_action(state, snapshot, about_it=True)) is not None:
            _point_to(u, kind)
            return u
        if not _explain_topic_again(snapshot, state, u):
            u.help = True         # no term explained last: the question on screen, in simpler words
        return u
    ext = snapshot.extensions
    # 19/11: the statement and the questions of one message are read apart ("אי לי הפרשות, מה זה?"): the zero is read
    # from what the message states, and a question next to it is still answered (before, the whole message was either
    # the zero — the question dropped — or, typed one letter short, a question only — the zero dropped)
    clauses = [c for c in _CLAUSE_RE.split(text) if c and c.strip()]
    asks = [c for c in clauses if "?" in c or _is_question(c) or asks_explanation(faq, c)]
    said = " ".join(c for c in clauses if c not in asks).strip() or (text if not asks else "")
    if (said and len(words(said)) <= 6 and not any(ch.isdigit() for ch in said)
            and not named_cars(said, snapshot.catalog)
            and (contributions_zero_said(said, ext.zero_cues, ext.contribution_terms, ext.nothing_deducted_phrases)
                 # a bare "no" ("לא מופרש", "אין", "כלום") to the question that asks both contributions, nothing else
                 or (asked_contributions(state)
                     and bare_zero_answer(said, ext.bare_zero_negatives, ext.bare_zero_words)))):
        # "אין הפרשות" (1/11): a zero for both contributions, confirmed once in code (both modes): never "not
        # understood", never read as an answer to another question, never saved without "לאשר"
        p = state.profile
        missing = [f for f in CONTRIBUTION_FIELDS if getattr(p, f) is None]
        if asks:
            # the question is understood as if asked alone (its explanation is part of the reply), the zero kept
            u = understand(state, snapshot, " ".join(c.strip() for c in asks))
            term = match_faq(faq, said)
            if (u.help or (refers_back(faq, " ".join(asks)) and not u.faq_ids)) and term is not None:
                # "…, מה זה?": "זה" is the term this message just said ("הפרשות"), never the question on screen
                u.help = False
                if term not in u.faq_ids:
                    u.faq_ids.append(term)
                u.topic = term
        u.zero_contributions, u.remainder = missing or list(CONTRIBUTION_FIELDS), ""
        return u
    if (state.eligible_unclear_asked and state.profile.group and len(words(text)) <= 4
            and _names_any((*faq.eligible_objects, "רכב"), [_spelling(w) for w in words(text)], starts=False)):
        # the answer to "the cars, or the net?" (16/11): the cars, counted by code
        u.eligible, u.topic, u.remainder = "", ELIGIBLE_TOPIC, ""
        return u
    fix = correction_request(snapshot, text)
    if fix is not None:
        # a correction of the employee's own data: code asks what (or the new value) and saves it like the form; it
        # is never a question for the representative, at any stage (1/11)
        u.correction, u.remainder = fix, ""
        return u
    kept: list[str] = []
    faq_clauses: list[tuple[str, str]] = []      # (entry, the clause it answered)
    catalog_clauses: list[tuple[str, str]] = []  # (catalog answer, the clause it answered)
    net_question = False
    net_clauses: list[str] = []                  # questions about the net no FAQ entry answers
    for clause in _CLAUSE_RE.split(text):
        if clause is None or not clause.strip():
            continue
        if any(cue in clause for cue in snapshot.extensions.all_at_once_cues):
            u.all_at_once = True                  # about the conversation itself: never the representative
            continue
        if faq.rep_request_cues and says_phrase(clause, faq.rep_request_cues):
            u.rep_request = True                  # "תשאל את הנציג": the offer to ask, never "not understood"
            continue
        clause_words = [_spelling(w) for w in words(clause)]
        if (faq.payslip_upload_terms and _names_any(("תלוש", "תלושים"), clause_words, starts=False)
                and _names_any(faq.payslip_upload_terms, clause_words, starts=False)):
            u.payslip_request = True              # "אני רוצה להעלות תלוש": the uploader, never the flow question
            continue
        if (faq.show_list_verbs and _names_any(faq.show_list_verbs, clause_words, starts=False)
                and _names_any(faq.show_list_objects, clause_words, starts=False)):
            u.show_list = True                    # "תציג לי אפשרויות": the catalog's cars (agent decides where)
            continue
        if ("?" in clause or _is_question(clause)) and not _RANK_RE.search(clause):
            eligible = asks_eligible(snapshot, state, clause)
            if eligible is not None and (state.profile.group or match_group(snapshot.calc.group_order, text)):
                # the cars open to the employee: code counts them after this turn's saves (the eligibility rules);
                # before the short follow-up reading, so "ועכשיו כמה?" after such an answer is counted again
                u.eligible, u.topic = eligible, ELIGIBLE_TOPIC
                continue
        short = [_spelling(w) for w in words(clause)]
        explain = asks_explanation(faq, clause)
        if "?" in clause and len(short) <= 3 and _names_any(faq.context_markers, short, starts=False) \
                and not any(ch.isdigit() for ch in clause) and not off_topic(snapshot, clause) \
                and match_faq(faq, clause, explain=explain) is None and not _strong(snapshot, clause, short) \
                and not _about_bot(faq, short):   # "מה זה השתתפות?": FAQ; "איך אתה יודע?": about the advisor
            if (kind := _offered_action(state, snapshot, about_it=True)) is not None:
                _point_to(u, kind)            # a follow-up to a reply about an action: that action (19/11)
            else:
                u.help = True                 # "ולמה?", "כלומר?", "והשני?": a follow-up to the bot's last message
            continue
        # "מה הכי משתלם לי?" is a request; "על מה מבוססות ההמלצות שלך?" is a question about the advisor
        if (_is_question(clause) or explain) and (not _RANK_RE.search(clause) or asks_basis(faq, clause)):
            if off_topic(snapshot, clause):
                u.redirect = True     # plainly not about this service: one redirect line, never the FAQ or the rep
                continue
            # the engine's figures for two calculated cars, and a fact the catalog holds (1/11): answered from them,
            # in both modes, before a general definition (the FAQ) or the representative
            if results_difference(state, snapshot, clause):
                # two calculated cars compared on the net: one mechanism answers it — the engine's comparison facts
                # for this pair (stage 2, 16/11), never a second, shorter answer next to them
                named = {c.catalog_code for c in named_cars(clause, snapshot.catalog)}
                u.about_results = True
                u.result_focus += [r.option_id for r in state.results if r.option_id in named
                                   and r.option_id not in u.result_focus]
                continue
            known = catalog_fact(snapshot, clause)
            if known:
                u.catalog.append(known)
                continue
            entry = match_faq(faq, clause, explain=explain)
            back = referent(snapshot, state) if refers_back(faq, clause) else None
            if back is not None and entry == "payslip" and back.where:
                # "איפה זה בתלוש?": where THAT value is, not where the payslip's data is in general (10/11)
                u.topic_lines.append((back.where, back.term))
                u.topic = back.id
                continue
            if back is not None and entry in faq.meta_entries:
                u.topic = back.id                     # "למה צריך את זה?": answered; "זה" stays the same term
            if (kind := actions.for_topic(state, snapshot, entry)) is not None:
                # "איך מעלים תלוש?" (19/11): the topic has an action on the screen — named and carried by the reply
                _point_to(u, kind)
            catalog = (catalog_answer(snapshot, clause, [r.option_id for r in state.results])
                       if entry is None else None)
            if entry is None and _is_help(clause):
                u.help = True
            elif catalog:
                u.catalog.append(catalog)
                catalog_clauses.append((catalog, clause))
            elif entry is None and (u.audit or u.map_all):    # a request ("מה כל האפשרויות שלי?"), not for the rep
                pass          # "כתבתי לך שיש לי רכב, למה אתה שואל שוב?": rhetorical; answered from the state
            elif entry is None:
                scope = question_scope(snapshot, clause)
                if explain and scope in ("net", "self", "unclear"):
                    # a term no entry covers, asked to be explained: never "why we ask" or the calculation (10/11);
                    # the term explained last, or the question on screen, in simpler words
                    u.help = True
                elif scope == "net" and u.faq_ids:
                    # "מה זה שווי שימוש ואיך הוא משפיע על הנטו שלי?": the effect of the term explained in this message
                    # is part of its explanation (the FAQ says what it means for the net), never a request to calculate
                    # or compare (RCA S1, 16/11)
                    pass
                elif scope == "net":
                    net_question = True               # "אני מרוויחה יותר?": the calculation answers it
                    net_clauses.append(clause)
                elif scope == "self":
                    if "why_ask" not in u.faq_ids and snapshot.faq.get("why_ask") is not None:
                        u.faq_ids.append("why_ask")   # about the employee's own data: why we ask, never the rep
                elif scope == "org":
                    u.unanswered.append(clause.strip())   # a concrete question: the representative may answer
                elif scope == "follow":
                    if (kind := _offered_action(state, snapshot, about_it=True)) is not None:
                        _point_to(u, kind)            # "איך עושים את זה?" after a reply about an action (19/11)
                    else:
                        u.help = True                 # a follow-up to the bot's last message: explained again
                elif scope == "off":
                    u.redirect = True                 # another domain ("קניית דירה"): the boundary line, never the rep
                elif scope == "bot":
                    if "how_it_works" not in u.faq_ids and snapshot.faq.get("how_it_works") is not None:
                        u.faq_ids.append("how_it_works")   # how the advisor works: only what it really uses
                elif (kind := _offered_action(state, snapshot, about_it=True)) is not None:
                    # an unclear follow-up right after a reply about an action (19/11): that action, never "not
                    # understood" and never the data question in its place
                    _point_to(u, kind)
                elif actions.names_control(clause) and (
                        kind := _offered_action(state, snapshot) or next(iter(actions.available(state, snapshot)), None)):
                    # a question about a control of the screen ("איפה הכפתור?"): the action the last reply offered, else
                    # the one the screen has — from the contract, never the model's words, never invented
                    _point_to(u, kind)
                else:
                    u.clarify = True                  # truly unclear: asked to say it again, never the rep
            elif entry not in u.faq_ids:
                u.faq_ids.append(entry)
                faq_clauses.append((entry, clause))
            continue
        if not _carries_value(snapshot, clause) and asks_attribute(snapshot, clause) \
                and not off_topic(snapshot, clause) and _choosing_cars(state):
            # a REQUEST for a property the catalog cannot verify ("אני רוצה רכב בצבע אדום", "בא לי גג נפתח"; 19/11) is
            # the same as asking about it: kept as the question nothing here answers, so the representative is offered
            # and a later "אפשר לשאול נציג על זה?" sends exactly it. Before, only the question form was ("יש בצבע
            # אדום?"): the request was read as a failed answer and the follow-up had nothing to send
            u.unanswered.append(clause.strip())
            continue
        if (not _carries_value(snapshot, clause)
                and (kind := actions.for_topic(state, snapshot, match_faq(faq, clause))) is not None):
            # a message about an action's topic that gives no value ("מעלה תלוש", 19/11): the action on the screen,
            # never "לא הצלחתי להבין" and never the data question alone
            _point_to(u, kind)
            continue
        kept.append(clause)
    # a question naming a value the calculation still needs ("איך אתה מחשב את הנטו בלי ההשתתפות?", 16/11) is about that
    # value: the missing-data facts answer it (data_facts), never the general entry its verb phrase matched ("איך אתה
    # מחשב" = how the advisor works; in a long question a one-word alias such as "השתתפות" is not matched). A question
    # that is only the field's own definition ("מה זה נקודות זיכוי?") keeps its entry
    about_missing = [(e, c) for e, c in faq_clauses
                     if (named := data_facts.named_missing(state, c)) and e not in named]
    if about_missing:
        u.missing_data = True
        gone = {e for e, _ in about_missing} - {e for e, c in faq_clauses if (e, c) not in about_missing}
        u.faq_ids = [e for e in u.faq_ids if e not in gone]
        faq_clauses = [(e, c) for e, c in faq_clauses if (e, c) not in about_missing]
    if u.help and _explain_topic_again(snapshot, state, u):
        u.help = False            # "מה זה אומר?" right after an explanation: that term again, in simpler words
    if u.topic is None:
        u.topic = next((i for i in u.faq_ids if i not in faq.meta_entries and i != "payslip"), None)
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
        # two results on screen compared ("מה ההבדל בין הדולפין לקונה?"): the engine's comparison, not the catalog's
        compared = [(a, c) for a, c in catalog_clauses if len(_about_results(snapshot, state, c)) >= 2]
        if compared:
            u.catalog = [a for a in u.catalog if a not in {a for a, _ in compared}]
            about += [c for _, c in compared]
        # a question about the net of a result on screen ("למה ב-MG4 יוצא לי יותר נטו?", 16/11): about that result,
        # answered from it — never a request to choose cars and calculate again
        on_screen = [c for c in net_clauses if _about_results(snapshot, state, c)]
        if on_screen:
            about += on_screen
            net_question = len(on_screen) < len(net_clauses)
        if about or u.help or u.clarify:
            u.about_results = True
            focus = [o for q in about for o in _about_results(snapshot, state, q)
                     if o != "" and len(_about_results(snapshot, state, q)) < len(state.results)]
            u.result_focus = list(dict.fromkeys([*u.result_focus, *focus]))   # added to, never replaced
            u.unanswered = [q for q in u.unanswered if q not in about]
            u.help = u.clarify = False
    remainder = "\n".join(c.strip() for c in kept).strip()      # clause boundaries stay (a first-line value)
    remainder = without_greeting(faq, remainder).strip()          # "היי, אני בקבוצה ג": the value only
    u.compare = _asks_to_compare(remainder) or u.map_all or net_question
    # a ranking / recommendation request ("הכי משתלם", "תמליץ", "דרג") is a request to rank ALL eligible
    # cars — distinct from a plain "תשווה ביניהם" that asks to compare a chosen set
    u.rank = bool(_RANK_RE.search(remainder))            # a ranking word inside a question about the advisor is not one
    if u.rank:
        u.compare = True
    for part in re.split(r"[,;]|\s+ו(?=אין|יש|גם)", remainder):
        for f in _presence(part):
            if f not in u.presence:
                u.presence.append(f)
    if u.presence:
        # the presence clauses carry nothing else to save
        remainder = ", ".join(p for p in re.split(r"[,;]", remainder) if not _presence(p)).strip()
    # a bare "אין" / "לא" / "אין לי" to the car question (16/11: the car is asked alone): "no car", or a car with no
    # copay? Asked, never guessed — and never matched to a car name ("לא" is not LEON)
    bare_no_to_car = (bool(_BARE_NO_RE.fullmatch(remainder)) and state.stage == Stage.COLLECT_CURRENT_CAR
                      and state.pending_confirmation is None and state.pending_clarification is None)
    if ((_NO_COPAY_RE.search(remainder) or bare_no_to_car) and state.current.source is None
            and not state.current.query and not state.current.has_car and not u.has_car
            and not named_cars(remainder, snapshot.catalog) and not no_car_span(remainder)):
        u.ambiguity = NO_CAR_OR_ZERO
        remainder = ""
    if u.audit and not re.search(r"\d", remainder) and "קבוצ" not in remainder:
        remainder = ""                            # "כבר כתבתי לך" carries nothing to save (but a group
        #                                           correction like "טעיתי, אני בקבוצה ד" does)
    u.remainder = remainder
    return u
