"""Verified comparison facts between two results on screen (stage 2 of the reply contract, 16/11).

"למה הקונה פחות משתלמת מה-MG4?" had no verified source: the engine explains each option against today's car only. Every
option is computed against the same today's car, so two options differ exactly by the difference of their engine results:
the nets, the breakdown values (use value, copay, maintenance, family share, electric refund) and the drivers' net effects.
The engine rounds the drivers so that their displayed effects add up to the displayed change; the pairwise differences
therefore add up to the displayed difference of the two nets. Nothing is calculated here beyond these subtractions, and
nothing is read from the employee's words: the pair is chosen from the results and the turn's focus.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.shared.money import to_shekel

from .stage import ConversationState, OptionResult

# a driver of the engine → (its name, how its value is shown on each side)
_COMPONENT = {
    "USE_VALUE": "שווי השימוש",
    "COPAY": "ההשתתפות",
    "MAINTENANCE": "אחזקת הרכב",
    "FAMILY_SHARE": "חלק העובד במסלול המשפחתי",
    "ELECTRIC": "החזר החשמל",
}
_VALUE = {"USE_VALUE": "use_value", "COPAY": "copay", "MAINTENANCE": "maintenance", "FAMILY_SHARE": "family_share"}


def _d(value: Any) -> Decimal:
    return Decimal(str(value))


def _money(value: Decimal) -> str:
    return f"{to_shekel(value):,} ₪"


def _change(delta: Decimal) -> str:
    """The engine's change against today's car, as on the cards."""
    if delta == 0:
        return "בלי שינוי לעומת היום"
    return f"{'תוספת' if delta > 0 else 'ירידה'} של {_money(abs(delta))} לעומת היום"


def _ok(item: OptionResult) -> bool:
    r = item.result
    return (r.get("status") == "OK" and r.get("delta_display") is not None
            and (r.get("alternative") or {}).get("net_display") is not None)


def pick_pair(state: ConversationState, focus: Sequence[str]) -> Optional[tuple[OptionResult, OptionResult]]:
    """The two results a "why" question compares: the two it names; one named and the highest estimated net (or, when the
    one named is the highest, the next one); none named: the first two. None with fewer than two results."""
    ok = sorted([i for i in state.results if _ok(i)], key=lambda i: -_d(i.result["delta_display"]))
    if len(ok) < 2:
        return None
    named = [i for i in ok if i.option_id in focus]
    if len(named) >= 2:
        return named[0], named[1]
    if len(named) == 1:
        other = ok[1] if named[0] is ok[0] else ok[0]
        return (named[0], other) if named[0] is ok[0] else (other, named[0])
    return ok[0], ok[1]


def _effects(result: dict[str, Any]) -> dict[str, Decimal]:
    """The engine's displayed net effect of each driver against today's car (electric refund as one component)."""
    out: dict[str, Decimal] = {}
    for d in result.get("drivers") or []:
        code = "ELECTRIC" if str(d["code"]).startswith("ELECTRIC") else str(d["code"])
        out[code] = out.get(code, Decimal(0)) + _d(d["net_effect_display"])
    return out


def _value(result: dict[str, Any], code: str) -> str:
    alt = result.get("alternative") or {}
    if code == "ELECTRIC":
        return "יש" if _d(alt.get("electric_gross_up") or 0) > 0 else "אין"
    return _money(_d(alt.get(_VALUE[code]) or 0))


@dataclass(frozen=True)
class PairFacts:
    net: str                 # the two nets and their difference (code's fact, always said as is)
    reasons: list[str]       # each component that differs, largest first (what the model may put in its words)

    def lines(self) -> list[str]:
        return [self.net, *self.reasons]


def pair_facts(a: OptionResult, b: OptionResult) -> PairFacts:
    """The engine's facts for A against B: the two estimated nets and their difference, then every component whose effect
    on the net differs, with its value on each side and the difference it makes (largest first)."""
    ra, rb = a.result, b.result
    net_a, net_b = _d(ra["alternative"]["net_display"]), _d(rb["alternative"]["net_display"])
    gap = net_a - net_b
    both = (f"{a.label}: נטו חודשי משוער {_money(net_a)} ({_change(_d(ra['delta_display']))}). "
            f"{b.label}: נטו חודשי משוער {_money(net_b)} ({_change(_d(rb['delta_display']))}).")
    if gap == 0:
        net = f"{both} ההפרש ביניהם: 0 ₪. הנטו זהה."
    else:
        high = a if gap > 0 else b
        net = f"{both} ההפרש ביניהם: {_money(abs(gap))} בחודש לטובת {high.label}."
    ea, eb = _effects(ra), _effects(rb)
    diffs = sorted(((c, ea.get(c, Decimal(0)) - eb.get(c, Decimal(0))) for c in set(ea) | set(eb)),
                   key=lambda x: -abs(x[1]))
    reasons = []
    for code, diff in diffs:
        if diff == 0 or code not in _COMPONENT:
            continue
        better = a.label if diff > 0 else b.label
        if not reasons:
            reasons.append(f"הסיבה העיקרית להפרש: {_COMPONENT[code]}.")       # the largest effect, first
        reasons.append(f"{_COMPONENT[code]}: {_value(ra, code)} ב-{a.label}, {_value(rb, code)} ב-{b.label}. "
                       f"בגלל זה הנטו ב-{better} גבוה ב-{_money(abs(diff))}.")
    return PairFacts(net=net, reasons=reasons)


def facts_block(pair: Optional[tuple[OptionResult, OptionResult]]) -> str:
    """The comparison facts for the model's prompt and for the claim verifier ("" without a pair)."""
    if pair is None:
        return ""
    return "COMPARISON FACTS (from the engine's results; the only source for why one option differs from another):\n" + \
        "\n".join(f"- {line}" for line in pair_facts(*pair).lines())
