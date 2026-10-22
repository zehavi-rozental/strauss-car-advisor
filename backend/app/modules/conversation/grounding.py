"""Grounding: a value the LLM asks to save must be something the employee actually said.

The code never trusts a tool call by itself (rule 2: the LLM extracts, code decides). A number must appear in
the message, as digits or as Hebrew number words ("שמונה עשר וחצי אלף"); a zero needs a zero cue ("אין", "בלי",
"0"); a group letter, an occupation, a drive and an alternative must be named. Pure functions, no state: the
agent refuses what is not grounded as a tool error and the employee is asked.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Iterable, Optional, Sequence

from app.modules.rules.public import words

from .parsing import numbers_in

HALF = Decimal("0.5")

_UNITS = {"אחד": 1, "אחת": 1, "שני": 2, "שניים": 2, "שתי": 2, "שתיים": 2, "שתים": 2, "שלוש": 3, "שלושה": 3,
          "ארבע": 4, "ארבעה": 4, "חמש": 5, "חמישה": 5, "שש": 6, "שישה": 6, "שבע": 7, "שבעה": 7, "שמונה": 8,
          "תשע": 9, "תשעה": 9, "עשר": 10, "עשרה": 10,
          # construct forms ("שלושת אלפים")
          "שלושת": 3, "ארבעת": 4, "חמשת": 5, "ששת": 6, "שבעת": 7, "שמונת": 8, "תשעת": 9, "עשרת": 10}
_TEN = {"עשר", "עשרה"}
_TENS = {"עשרים": 20, "שלושים": 30, "ארבעים": 40, "חמישים": 50, "שישים": 60, "שבעים": 70, "שמונים": 80,
         "תשעים": 90}
_HUNDREDS = {"מאה": 100, "מאתיים": 200}
_THOUSANDS = {"אלף": 1000, "אלפיים": 2000, "אלפים": 1000}
_ALL = {*_UNITS, *_TENS, *_HUNDREDS, *_THOUSANDS, "מאות", "חצי"}
_PREFIX = "ו"


def _tokens(text: str) -> list[str]:
    """Words of the message; a leading "ו" ("וחצי", "וחמש") is split off when the rest is a number word."""
    out: list[str] = []
    for w in words(text.replace("-", " ")):
        if w not in _ALL and len(w) > 2 and w[0] == _PREFIX and w[1:] in _ALL:
            w = w[1:]
        out.append(w)
    return out


def word_numbers(text: str) -> set[Decimal]:
    """Numbers said in Hebrew words, one value per run of number words: "שני" 2, "עשרים וחמש" 25,
    "שמונה עשר וחצי" 18.5, "שמונה עשר וחצי אלף" 18,500, "שלוש מאות" 300, "עשרים אלף" 20,000."""
    found: set[Decimal] = set()
    total = cur = 0
    half = seen = False
    toks = _tokens(text)
    i = 0
    while i <= len(toks):
        tok = toks[i] if i < len(toks) else None
        if tok in _ALL:
            seen = True
            if tok in _UNITS:
                if tok not in _TEN and i + 1 < len(toks) and toks[i + 1] in _TEN:
                    cur += 10 + _UNITS[tok]            # "שמונה עשר" 18, "שתים עשרה" 12
                    i += 1
                else:
                    cur += _UNITS[tok]
            elif tok in _TENS:
                cur += _TENS[tok]
            elif tok in _HUNDREDS:
                cur += _HUNDREDS[tok]
            elif tok == "מאות":
                cur = (cur or 1) * 100                  # "שלוש מאות"
            elif tok == "חצי":
                half = True
            else:                                       # אלף / אלפיים / אלפים
                base = Decimal(cur) + (HALF if half else 0)
                total += int((base or 1) * _THOUSANDS[tok]) if tok != "אלפיים" else 2000
                cur, half = 0, False
        elif seen:
            value = Decimal(total + cur) + (HALF if half else 0)
            found.add(value)
            total = cur = 0
            half = seen = False
        i += 1
    return found


# Two amounts offered as alternatives ("18,000 או 18,500", "45 או 45 אלף", "300 או 400 ₪"): the employee has not
# decided between them, so neither is said as a fact (live QA 22/10: "האם 45 או 45 אלף?" saved 45,000).
_AMOUNT = r"\d[\d,]*(?:\.\d+)?(?:\s*(?:אלף|אלפים|[kK]))?(?:\s*(?:₪|ש\"ח|שח|שקל(?:ים)?))?"
_ALTERNATIVES_RE = re.compile(rf"{_AMOUNT}\s+או\s+{_AMOUNT}")


def without_alternatives(text: str) -> str:
    """The message without amounts offered as alternatives: what is left is what the employee stated."""
    return _ALTERNATIVES_RE.sub(" ", text)


def said_numbers(text: str) -> set[Decimal]:
    """Every number the message contains: digits and Hebrew number words."""
    return {*numbers_in(text), *word_numbers(text)}


def grounded_number(value: object, text: str) -> bool:
    """The value is in the message: as said, "18.5" / "18 וחצי" / "18 אלף" for 18,500, "20 אלף" for 20,000.
    A bare "2" never grounds 2,000 (a shortened amount is asked, not multiplied)."""
    try:
        v = Decimal(str(value))
    except Exception:  # noqa: BLE001 — not a number
        return False
    ws = set(_tokens(text))
    half = "חצי" in ws
    thousand = bool(ws & set(_THOUSANDS)) or bool(re.search(r"\d\s*[kK](?![א-תA-Za-z])", text))
    for n in said_numbers(text):
        options = {n}
        if half:
            options.add(n + HALF)
        for base in tuple(options):
            if thousand or base != base.to_integral_value():
                options.add(base * 1000)
        if v in options:
            return True
    return False


def has_zero_cue(text: str, cues: Iterable[str]) -> bool:
    """A zero is said: "אין", "בלי", "ללא", "אפס", "0", "לא משלמת"..."""
    if Decimal(0) in numbers_in(text):
        return True
    toks = words(text)
    expanded = toks + [t[1:] for t in toks if len(t) > 2 and t[0] == _PREFIX]
    joined = f" {' '.join(expanded)} "
    return any(f" {' '.join(words(c))} " in joined for c in cues if words(c))


# the words that name each field in a message ("אין עמלות", "בלי השתלמות"): a zero is grounded for a field only
# when its cue comes with the field's word (or the message is a bare "אין" and the field may be answered so)
ZERO_WORDS = {
    "commissions": ("עמל",), "bonus": ("בונוס", "פרמיה"), "study_fund_percent": ("השתלמות",),
    "pension_percent": ("פנסי",), "credit_points": ("זיכוי", "נקוד"), "children_under_23": ("ילד",),
    "current_copay": ("משתת", "משלם", "משלמת", "השתתפות", "תשלום"), "current_use_value": ("שווי",),
    "current_maintenance": ("אחזק",), "base_salary": ("שכר", "משכורת"),
}
# a bare "אין" answers the Q2 items where "none" is a normal answer; pension and credit points are never assumed
BARE_ZERO_OK = {"commissions", "bonus", "study_fund_percent", "children_under_23"}
_BARE_FILLERS = {"לי", "כלום", "בכלל", "אצלי", "פה"}


# words that may stand between a zero cue and the field's word ("אין לי קרן השתלמות", "אפס נקודות זיכוי")
_BETWEEN = {"לי", "לנו", "בכלל", "שום", "קרן", "נקודות", "נקודת", "הפרשה", "הפרשות", "ל", "את", "עליו", "עליה"}


def _zero_next_to_field(field: str, toks: list[str], cues: Sequence[str]) -> bool:
    """The zero belongs to this field: a cue right before its word (only connecting words between: "אין לי
    ילדים", "ואין בונוס"), or a zero right after it ("ילדים: 0"). A cue for another phrase does not reach
    across to a word joined with "ו" ("אין נקודות זיכוי וילדים" is not "no children")."""
    stems = ZERO_WORDS.get(field, ())
    seqs = [words(c) for c in cues if words(c)]

    def bare(t: str) -> str:
        return t[1:] if len(t) > 2 and t[0] == _PREFIX else t

    for i, tok in enumerate(toks):
        if not any(s in tok for s in stems):
            continue
        if toks[i + 1:i + 2] and toks[i + 1] in ("0", "אפס", "אין"):
            return True
        for seq in seqs:                      # a cue that includes the field's word: "לא משלמת"
            n = len(seq)
            if i - n + 1 >= 0 and [bare(t) for t in toks[i - n + 1:i + 1]] == seq:
                return True
        j = i - 1
        while j >= 0 and toks[j] in _BETWEEN:
            j -= 1
        for seq in [*seqs, ["0"]]:
            n = len(seq)
            if j - n + 1 >= 0 and [bare(t) for t in toks[j - n + 1:j + 1]] == seq:
                return True
        # "אין לי עמלות או בונוס": a negative reaches across "או" to the next field (neither one). "ו" stays as
        # it was: "אין נקודות זיכוי וילדים" is not "no children". Live QA 22/10.
        if i >= 2 and toks[i - 1] == "או":
            for words_back in (1, 2):                 # the other field's word(s): "עמלות", "נקודות זיכוי"
                m = i - 2 - words_back                # the token before them
                while m >= 0 and toks[m] in _BETWEEN:
                    m -= 1
                if any(m - len(seq) + 1 >= 0 and [bare(t) for t in toks[m - len(seq) + 1:m + 1]] == seq
                       for seq in seqs):
                    return True
        # "עמלות ובונוס אין לי": the negative after a list joined with "ו" covers each field in it; never after
        # "יש" ("יש לי עמלות ובונוס אין" has commissions)
        if "יש" not in toks[max(0, i - 3):i]:
            for k in range(i + 1, min(i + 4, len(toks))):
                if toks[k] in ("0", "אפס", "אין"):
                    if toks[i + 1].startswith("ו") and len(toks[i + 1]) > 2:
                        return True
                    break
    return False


def zero_grounded(field: str, text: str, cues: Sequence[str]) -> bool:
    """A zero for this field is said: a zero cue ("אין", "בלי", "ללא", "אפס", "0") together with the field's word
    ("אין עמלות", "ילדים: 0"), or a bare "אין" for a field that may be answered so. A cue for another field is
    not a zero for this one."""
    if not has_zero_cue(text, cues):
        return False
    if _zero_next_to_field(field, words(text.lower()), cues):
        return True
    cue_tokens = {t for c in cues for t in words(c)} | _BARE_FILLERS | {"0"}
    toks = [t[1:] if len(t) > 2 and t[0] == _PREFIX and t[1:] in cue_tokens else t for t in words(text)]
    return field in BARE_ZERO_OK and bool(toks) and all(t in cue_tokens for t in toks)


def group_named(text: str, groups: Sequence[str], family: str) -> bool:
    """A group letter ("קבוצה ג", "ג", "ב-ג") or the family track is in the message."""
    toks = words(text)
    expanded = {*toks, *(t[1:] for t in toks if len(t) > 1 and t[0] in "וב")}
    return bool(expanded & set(groups)) or family in text


def occupation_named(name: str, text: str, from_text: Optional[str]) -> bool:
    """The occupation matched in the table is the one the message says (the reader found it), or the message
    shares a word of at least three letters with it ("מנהלת בכירה למוצר" for "מנהל/ת מוצר")."""
    if from_text is not None and from_text == name:
        return True
    stems = [w for w in words(name.replace("/", " ")) if len(w) >= 3]
    toks = words(text)
    return any(stem in tok for stem in stems for tok in toks)


def option_named(codes: Iterable[str], named_codes: set[str], text: str, rank_words: Iterable[str]) -> bool:
    """An alternative is chosen by naming it, by its number in the list shown, or by asking for the ranking."""
    codes = list(codes)
    if codes and set(codes) <= named_codes:
        return True
    if not named_codes and (re.search(r"\d", text) or any(w in text for w in rank_words)):
        return True
    return False
