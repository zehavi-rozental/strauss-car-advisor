"""The actions the employee's screen really offers (19/11): one contract between the server and the client.

An action is offered only when code decides it is available now; the client draws a button only for an action in
`expects.actions` (never from words in a reply), and a reply may name a button only if it is one of them. Each action
belongs to a FAQ topic, so a message or a question about that topic ("מעלה תלוש", "איך מעלים תלוש?") points to the real
action, and a follow-up to a reply that offered it ("איפה הכפתור?", "איך עושים את זה?") is about it — whatever the words,
without a list of phrases.
"""
from __future__ import annotations

from typing import Any

from app.modules.rules.public import RulesSnapshot

from .stage import PROFILE_FIELDS, ConversationState, Stage, next_stage

PAYSLIP_UPLOAD = "payslip_upload"
# the label is the button's own text on the screen (the client draws exactly this)
LABELS = {PAYSLIP_UPLOAD: "להעלות תלוש שכר"}
# the FAQ topic each action belongs to (faq.yaml ids)
TOPICS = {PAYSLIP_UPLOAD: "payslip"}
# where it is and what it does, in the employee's words; the place matches the client (EmployeePage, persistent actions)
HELP = {PAYSLIP_UPLOAD: ("הכפתור \"להעלות תלוש שכר\" נמצא מתחת להודעות, מעל תיבת הכתיבה. לוחצים עליו ובוחרים קובץ PDF "
                         "או תמונה של התלוש האחרון; אציג את הנתונים שקראתי לבדיקה ולתיקון, ורק אחרי שתאשרו אשתמש בהם. "
                         "התלוש עצמו לא נשמר. אפשר גם פשוט להמשיך לענות כאן בשיחה.")}


# the words that name a control on the screen: a question with one is about the screen's actions (understanding), and a
# reply may use one only for an action the screen has (guards.ui_violations). The vocabulary of the contract itself,
# not of any one question
UI_WORDS = ("כפתור", "לחצן", "קישור", "טופס")


def names_control(text: str) -> bool:
    """The text names a control of the screen ("איפה הכפתור?", "אין לי קישור")."""
    return any(w in text for w in UI_WORDS)


def upload_enabled(snapshot: RulesSnapshot) -> bool:
    """The payslip uploader is configured (the built-in one, or a pilot's external page)."""
    ext = snapshot.extensions
    return bool(ext.payslip_upload_enabled or ext.payslip_upload_url)


def payslip_possible(state: ConversationState, snapshot: RulesSnapshot) -> bool:
    """A payslip can be uploaded now if the employee asks for it: the uploader runs here and nothing waits for the
    representative. Asked for, it is offered at any stage (a new payslip, a replacement, updated salary details)."""
    return upload_enabled(snapshot) and next_stage(state) != Stage.WAITING_FOR_REP


def payslip_allowed(state: ConversationState, snapshot: RulesSnapshot) -> bool:
    """The upload is on the screen by itself (19/11): before any payslip was confirmed, while a salary value is still
    missing and no result is shown; once one was confirmed it is hidden (what is left is asked in the chat, and
    "הנתונים שלי" changes a value). Asked for again ("להעלות תלוש חדש", "לעדכן את נתוני השכר"), it is back."""
    if not payslip_possible(state, snapshot):
        return False
    if state.payslip_requested:
        return True
    if state.payslip_reviewed or state.results:
        return False
    return any(getattr(state.profile, f) is None for f in PROFILE_FIELDS)


def available(state: ConversationState, snapshot: RulesSnapshot) -> list[str]:
    """The actions on the employee's screen now, in the order they are drawn."""
    return [PAYSLIP_UPLOAD] if payslip_allowed(state, snapshot) else []


def offerable(state: ConversationState, snapshot: RulesSnapshot) -> list[str]:
    """The actions a message about their topic may bring back now (shown, or hidden but possible on request)."""
    return [PAYSLIP_UPLOAD] if payslip_possible(state, snapshot) else []


def for_topic(state: ConversationState, snapshot: RulesSnapshot, topic: str | None) -> str | None:
    """The action that belongs to this FAQ topic and can be offered now, if any (19/11: also one hidden after a
    confirmed payslip — "מעלה תלוש חדש" brings it back, carried by the reply)."""
    return next((a for a in offerable(state, snapshot) if TOPICS.get(a) == topic), None) if topic else None


def expects_actions(state: ConversationState, snapshot: RulesSnapshot) -> list[dict[str, Any]]:
    """`expects.actions`: what the client may draw as buttons (a pilot's external upload page goes with its url)."""
    out = []
    for kind in available(state, snapshot):
        item: dict[str, Any] = {"kind": kind, "label": LABELS[kind]}
        if kind == PAYSLIP_UPLOAD and snapshot.extensions.payslip_upload_url:
            item["url"] = snapshot.extensions.payslip_upload_url
        out.append(item)
    return out
