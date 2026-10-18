"""faq.yaml: grounded answers to the employee's questions.

Numbers are never written in the file: {placeholders} are filled from strauss-rules.json, so the
answers follow the rules. A wrong schema or an unknown placeholder fails loading with a Hebrew error.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

FAQ_FILE = "faq.yaml"


class FaqEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    term: str                                  # words a reply that answers this entry must contain
    aliases: tuple[str, ...] = ()
    answer: str
    simple: Optional[str] = None               # the question again, in simpler words
    example: Optional[str] = None              # what to type


class Faq(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = ""
    entries: tuple[FaqEntry, ...] = ()
    redirect: str = "אני כאן רק כדי לעזור עם הרכב הצמוד והנטו שלך."
    injection_patterns: tuple[str, ...] = ()
    off_topic_patterns: tuple[str, ...] = ()

    def get(self, entry_id: str) -> Optional[FaqEntry]:
        return next((e for e in self.entries if e.id == entry_id), None)

    @property
    def ids(self) -> list[str]:
        return [e.id for e in self.entries]


def _plain(value: Decimal) -> str:
    text = f"{value.normalize():f}"
    return f"{int(value):,}" if value == int(value) and abs(value) >= 1000 else text


def faq_values(strauss: Any, max_options: int) -> dict[str, str]:
    ft = strauss.family_track
    return {
        "electric_net_bonus": _plain(Decimal(str(strauss.electric.net_bonus))),
        "credit_point_value": _plain(Decimal(str(strauss.net_method.credit_point_value))),
        "family_min_children": str(ft.min_children),
        "child_max_age": str(ft.child_max_age),
        "family_gap_percent": _plain(Decimal(str(ft.gap_split_employee)) * 100),
        "groups": "-".join([strauss.group_order[0], strauss.group_order[-1]]),
        "max_options": str(max_options),
    }


def load_faq(config_dir: Path, values: dict[str, str]) -> Faq:
    """Missing file → an empty FAQ (every question goes to the representative)."""
    from .loader import ConfigError, _schema_error

    path = config_dir / FAQ_FILE
    if not path.exists():
        return Faq()
    try:
        faq = Faq.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise _schema_error(FAQ_FILE, exc) from exc
    if len(set(faq.ids)) != len(faq.ids):
        raise ConfigError(f"{FAQ_FILE}: מזהה כפול ברשימת השאלות")
    entries = []
    for e in faq.entries:
        try:
            filled = {k: (getattr(e, k).format(**values) if getattr(e, k) else getattr(e, k))
                      for k in ("answer", "simple", "example")}
        except KeyError as exc:
            raise ConfigError(f"{FAQ_FILE}: ערך לא מוכר {{{exc.args[0]}}} בשאלה {e.id}") from exc
        entries.append(e.model_copy(update=filled))
    return faq.model_copy(update={"entries": tuple(entries)})


__all__ = ["FAQ_FILE", "Faq", "FaqEntry", "faq_values", "load_faq"]
