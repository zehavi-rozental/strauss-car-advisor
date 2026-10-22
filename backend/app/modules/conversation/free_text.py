"""Free text the employee typed, before it is saved as a role or a car name (P0-B, QA 22/10).

A role outside the occupations table and a car outside the catalog and the tax file are kept as text only
when the text reads as one: a short name, not a sentence, a question, a greeting, a topic of this service
or something personal ("סבבה, הבנתי", "שוי שמוש", "הילד שלי חולה" are none of them). Otherwise nothing is
saved and the question is asked again. The same checks guard every write (template mode, the LLM, a
confirmation button) and what reaches the representative.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from app.modules.rules.public import RulesSnapshot, catalog_names, mentions, words

from .parsing import none_span, occupation_variants, unknown_span

TITLE_MAX_WORDS = 4
CAR_MAX_WORDS = 5
NAME_MAX_CHARS = 40
_PREFIX = "והבלמשכ"
_LATIN_RE = re.compile(r"[A-Za-z]")
# words that mean the text is about pay, not about a car: never sent to the representative
_PAY_WORDS = ("שכר", "משכורת", "בונוס", "עמלה", "עמלות", "פנסיה", "ברוטו", "נטו", "השתלמות", "תלוש", "מרוויח")
# a sentence, not a name: question / exclamation marks, a comma or a full stop between words ("מ.ג" is a name)
_SENTENCE_RE = re.compile(r"[?!,;:\n]|\.(?:\s|$)")


def _forms(word: str) -> list[str]:
    """The word and the word without one prefix letter ("הילד" → "ילד")."""
    return [word, word[1:]] if len(word) > 2 and word[0] in _PREFIX else [word]


def _close(a: str, b: str) -> bool:
    """The same word, or a typo of it in a longer word ("שוי" ~ "שווי" no; "שמוש" ~ "שימוש" yes)."""
    return a == b or (len(a) >= 4 and len(b) >= 3 and SequenceMatcher(None, a, b).ratio() >= 0.8)


def _has_word(ws: list[str], listed: set[str]) -> bool:
    return any(f in listed for w in ws for f in _forms(w))


def _name_shape(text: str, max_words: int) -> bool:
    """A short name: a few words, no sentence punctuation."""
    t = " ".join(str(text or "").split())
    return 0 < len(words(t)) <= max_words and len(t) <= NAME_MAX_CHARS and not _SENTENCE_RE.search(t)


def _names_topic(snapshot: RulesSnapshot, ws: list[str]) -> bool:
    """A topic of this service, typos tolerated ("שוי שמוש" is "שווי שימוש", "נקדות זכוי" is "נקודות זיכוי")."""
    for entry in snapshot.faq.entries:
        for alias in (*entry.aliases, entry.term):
            aw = words(alias)
            if aw and all(any(_close(a, f) or (len(aw) > 1 and SequenceMatcher(None, a, f).ratio() >= 0.75)
                              for w in ws for f in _forms(w)) for a in aw):
                return True
    return False


def _title_head(snapshot: RulesSnapshot, ws: list[str]) -> bool:
    """Starts like a title in the occupations table ("מנהלת", "רכזת", "נציג") or in `title_words` ("חשב"):
    plainly a role."""
    heads = {v.split()[0] for name in snapshot.occupations for v in occupation_variants(name) if v.split()}
    heads |= {w for t in snapshot.extensions.title_words for w in words(t)[:1]}
    return any(f.startswith(h) for w in ws[:1] for f in _forms(w) for h in heads if len(h) >= 3)


def _names_car(snapshot: RulesSnapshot, text: str, ws: list[str]) -> bool:
    """A catalog car, or a manufacturer by name (the catalog's, `manufacturer_brands`, `car_brands`)."""
    ext = snapshot.extensions
    brands = {b for names in ext.manufacturer_brands.values() for b in names} | set(ext.car_brands)
    brands |= {car.manufacturer.split()[0] for car in snapshot.catalog if car.manufacturer.split()}
    return (any(mentions(text, n) for car in snapshot.catalog for n in catalog_names(car))
            or any(all(any(f == bw for w in ws for f in _forms(w)) for bw in words(b)) for b in brands if words(b)))


def looks_like_title(snapshot: RulesSnapshot, text: str) -> bool:
    """A job title the employee may confirm although it is not in the table ("קופאית"): a short name with
    no figures; not a sentence, greeting, question or personal remark; not "לא יודע" / "אין"; not a car,
    and not a topic of this service unless it starts like a title in the table ("מנהל שכר")."""
    if not _name_shape(text, TITLE_MAX_WORDS) or any(ch.isdigit() for ch in text):
        return False
    ws = words(text)
    if unknown_span(text) or none_span(text) or _has_word(ws, set(snapshot.extensions.non_title_words)):
        return False
    if _names_car(snapshot, text, ws):
        return False
    return _title_head(snapshot, ws) or not _names_topic(snapshot, ws)


def looks_like_car(snapshot: RulesSnapshot, text: str) -> bool:
    """A car name not found in the catalog or the tax file ("טויוטה מיסטרי", "Swift 2019"): a short name
    that names a manufacturer or is written in Latin letters (the tax file's names); figures only as a
    model number or a year. Anything else ("הילד שלי חולה") is not kept as a car."""
    if not _name_shape(text, CAR_MAX_WORDS) or any(w in text for w in _PAY_WORDS):
        return False
    for token in re.findall(r"(?<![A-Za-z])\d[\d,.]*", text):     # "C200" is a model code, "150" is a figure
        if not token.isdigit() or not (len(token) <= 2 or (len(token) == 4 and 1980 <= int(token) <= 2035)):
            return False
    ws = words(text)
    ext = snapshot.extensions
    if _has_word([w for w in ws if w not in ext.car_message_fillers], set(ext.non_title_words)):
        return False                     # "יש לי פיאט" is a car; "סבבה פיאט" is not a name
    return bool(_LATIN_RE.search(text)) or _names_car(snapshot, text, ws)
