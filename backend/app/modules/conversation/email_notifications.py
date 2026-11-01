"""Encrypted email addresses, one-time conversation links, and a retryable notification outbox."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import secrets
import smtplib
import ssl
import uuid
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.shared.db import utcnow

from .models import ConversationRow, EmailLinkRow, EmailOutboxRow, MessageRow
from .repository import token_hash

log = logging.getLogger(__name__)

EMAIL_RE = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
MAX_ATTEMPTS = 8
LINK_TTL = timedelta(hours=24)
MAX_VERIFY_SENDS = 5          # verification links per conversation and address (a new one only when none is valid)
# an answer notice for an address not verified yet: kept out of the sending queue (it never blocks other mail) and
# released to PENDING when the address is verified
AWAITING = "AWAITING"


class EmailNotConfigured(Exception):
    pass


def local_url(url: str) -> bool:
    """A link only this computer can open (localhost / 127.x / no host)."""
    host = (urlparse(url).hostname or "").lower()
    return host in ("", "localhost", "0.0.0.0", "::1") or host.startswith("127.")


# a long number in the employee's question may be pay data: such a question is not copied into an email (1/11)
_LONG_NUMBER_RE = re.compile(r"\d[\d,.]{2,}")
_YEAR_RE = re.compile(r"(?<!\d)20\d\d(?!\d)")


def question_for_email(text: str) -> str:
    """The employee's own question, safe to put in an email: at most 300 characters, and nothing at all when it
    carries a figure that is not a model year (it may be a salary; the email then points to the conversation)."""
    text = " ".join(text.split())
    if _LONG_NUMBER_RE.search(_YEAR_RE.sub("", text)):
        return ""
    return text[:300] + ("…" if len(text) > 300 else "")


class InvalidEmailLink(Exception):
    pass


class EmailNotificationService:
    def __init__(self, sessions: sessionmaker, settings: Any):
        self.sessions = sessions
        self.settings = settings
        self.fake_messages: list[dict[str, str]] = []
        if settings.email_backend == "disabled":
            self.fernet = None
        elif not settings.email_encryption_key:
            self.fernet = None
        else:
            try:
                self.fernet = Fernet(settings.email_encryption_key.encode("ascii"))
            except (ValueError, UnicodeEncodeError) as exc:
                raise EmailNotConfigured("EMAIL_ENCRYPTION_KEY must be a valid Fernet key") from exc
        if settings.email_backend == "smtp" and not (
            settings.email_smtp_host and settings.email_from and settings.email_public_app_url
        ):
            raise EmailNotConfigured("SMTP host, sender, and public app URL are required")

    @property
    def enabled(self) -> bool:
        """Mail can be sent: a fake or SMTP backend with a usable key. Otherwise the conversation and the
        representative work as usual, and nothing is said about email. On Render (production) a link only a local
        computer can open is never mailed: mail stays off and the reason is logged once (1/11)."""
        if (self.settings.email_backend == "smtp" and os.environ.get("RENDER")
                and local_url(self.settings.email_public_app_url)):
            if not getattr(self, "_local_url_logged", False):
                log.error("EMAIL_PUBLIC_APP_URL is a local address on Render: email notices are off")
                self._local_url_logged = True
            return False
        return self.settings.email_backend in ("fake", "smtp") and self.fernet is not None

    @property
    def sends_inline(self) -> bool:
        """The fake backend delivers within the request (tests, local runs); SMTP only from the worker, so a slow
        mail server never holds the representative's answer or the employee's inquiry."""
        return self.settings.email_backend == "fake"

    @staticmethod
    def normalize_email(value: str) -> str:
        import re

        email = value.strip().casefold()
        if len(email) > 254 or not re.fullmatch(EMAIL_RE, email):
            raise ValueError("כתובת האימייל אינה תקינה.")
        return email

    def _encrypt(self, value: str) -> str:
        if self.fernet is None:
            raise EmailNotConfigured("Email delivery and EMAIL_ENCRYPTION_KEY are not configured")
        return self.fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def _decrypt(self, value: str) -> str:
        if self.fernet is None:
            raise EmailNotConfigured("Email delivery and EMAIL_ENCRYPTION_KEY are not configured")
        try:
            return self.fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeEncodeError) as exc:
            raise EmailNotConfigured("Stored email data cannot be decrypted with EMAIL_ENCRYPTION_KEY") from exc

    def prepare_verification(self, session, conversation: ConversationRow, email: str,
                             question_id: str | None = None) -> str:
        """A verification link for this address, unless one is still valid (never two at once). An expired or
        undeliverable one is replaced, at most MAX_VERIFY_SENDS times per conversation and address. An address
        verified for this conversation is not verified again. Returns what happened: "queued" (a new link),
        "valid" (the previous link still works), "limit" (no more links), "verified" (nothing to do)."""
        email = self.normalize_email(email)
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        if conversation.email_verified_at:
            if not conversation.email_ciphertext or self._decrypt(conversation.email_ciphertext) != email:
                raise ValueError("כתובת האימייל כבר אומתה ואי אפשר להחליף אותה דרך הפנייה.")
            return "verified"
        conversation.email_ciphertext = self._encrypt(email)
        now = utcnow()
        sent = list(session.scalars(select(EmailOutboxRow).where(
            EmailOutboxRow.conversation_id == conversation.id, EmailOutboxRow.purpose == "VERIFY",
            EmailOutboxRow.dedupe_key.startswith(f"verify:{conversation.id}:{digest}"))))
        live = session.scalar(select(EmailLinkRow.id).where(
            EmailLinkRow.conversation_id == conversation.id, EmailLinkRow.purpose == "VERIFY",
            EmailLinkRow.email_hash == digest, EmailLinkRow.consumed_at.is_(None), EmailLinkRow.expires_at > now))
        if live is not None and any(r.status != "FAILED" for r in sent):
            return "valid"
        if len(sent) >= MAX_VERIFY_SENDS:
            return "limit"
        dedupe_key = f"verify:{conversation.id}:{digest}" + (f":{len(sent)}" if sent else "")
        self._queue(session, conversation, email, "VERIFY", question_id, dedupe_key, digest)
        return "queued"

    def prepare_answer(self, session, conversation: ConversationRow, question_id: str) -> None:
        """One notice per answer. Before the address is verified it waits outside the queue (AWAITING)."""
        if not conversation.email_ciphertext or not self.enabled:
            return
        email = self._decrypt(conversation.email_ciphertext)
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        dedupe_key = f"answer:{question_id}"
        if session.scalar(select(EmailOutboxRow.id).where(EmailOutboxRow.dedupe_key == dedupe_key)):
            return
        self._queue(session, conversation, email, "ANSWER", question_id, dedupe_key, digest,
                    status="PENDING" if conversation.email_verified_at else AWAITING)

    @staticmethod
    def verification_status(session, conversation: ConversationRow) -> str:
        if conversation.email_verified_at:
            return "verified"
        row = session.scalar(
            select(EmailOutboxRow).where(EmailOutboxRow.conversation_id == conversation.id,
                                         EmailOutboxRow.purpose == "VERIFY")
            .order_by(EmailOutboxRow.created_at.desc()).limit(1)
        )
        if row is None:
            return "pending"
        return {"SENT": "sent", "FAILED": "failed"}.get(row.status, "pending")

    def _queue(self, session, conversation: ConversationRow, email: str, purpose: str,
               question_id: str | None, dedupe_key: str, email_hash: str, status: str = "PENDING") -> None:
        raw_token = secrets.token_urlsafe(32)
        session.add(EmailLinkRow(
            conversation_id=conversation.id, question_id=question_id, purpose=purpose,
            token_hash=token_hash(raw_token), email_hash=email_hash, expires_at=utcnow() + LINK_TTL))
        session.add(EmailOutboxRow(
            dedupe_key=dedupe_key, conversation_id=conversation.id, question_id=question_id, purpose=purpose,
            recipient_ciphertext=self._encrypt(email), token_ciphertext=self._encrypt(raw_token),
            status=status, attempts=0, next_attempt_at=utcnow()))

    def redeem(self, raw_token: str) -> tuple[str, str, str | None]:
        if not raw_token or len(raw_token) > 200:
            raise InvalidEmailLink()
        now = utcnow()
        new_session_token = secrets.token_urlsafe(32)
        with self.sessions() as session, session.begin():
            link = session.scalar(select(EmailLinkRow).where(EmailLinkRow.token_hash == token_hash(raw_token)))
            if link is None or link.consumed_at is not None or link.expires_at <= now:
                raise InvalidEmailLink()
            conversation = session.get(ConversationRow, link.conversation_id)
            if conversation is None or not conversation.email_ciphertext:
                raise InvalidEmailLink()
            email = self._decrypt(conversation.email_ciphertext)
            if hashlib.sha256(email.encode("utf-8")).hexdigest() != link.email_hash:
                raise InvalidEmailLink()
            if link.purpose == "VERIFY":
                conversation.email_verified_at = now
                # answer notices that waited for this verification join the queue now
                session.execute(
                    update(EmailOutboxRow)
                    .where(EmailOutboxRow.conversation_id == conversation.id, EmailOutboxRow.status == AWAITING)
                    .values(status="PENDING", next_attempt_at=now))
            elif not conversation.email_verified_at:
                raise InvalidEmailLink()
            consumed = session.execute(
                update(EmailLinkRow)
                .where(EmailLinkRow.id == link.id, EmailLinkRow.consumed_at.is_(None),
                       EmailLinkRow.expires_at > now)
                .values(consumed_at=now)
            )
            if consumed.rowcount != 1:
                raise InvalidEmailLink()
            conversation.additional_session_token_hashes = (
                list(conversation.additional_session_token_hashes or []) + [token_hash(new_session_token)]
            )[-10:]
            conversation_id = str(conversation.id)
            question_id = link.question_id
        return conversation_id, new_session_token, question_id

    def process_pending(self, limit: int = 25) -> int:
        now = utcnow()
        with self.sessions() as session, session.begin():
            session.execute(
                update(EmailOutboxRow)
                .where(EmailOutboxRow.status == "SENDING", EmailOutboxRow.next_attempt_at <= now,
                       EmailOutboxRow.attempts >= MAX_ATTEMPTS)
                .values(status="FAILED", last_error="delivery_interrupted")
            )
            session.execute(
                update(EmailOutboxRow)
                .where(EmailOutboxRow.status == "SENDING", EmailOutboxRow.next_attempt_at <= now,
                       EmailOutboxRow.attempts < MAX_ATTEMPTS)
                .values(status="RETRY")
            )
            # answer notices of addresses not verified yet (also ones queued before AWAITING existed) leave the
            # queue before it is read, so they can never fill the batch and hold back mail that can be sent
            session.execute(
                update(EmailOutboxRow)
                .where(EmailOutboxRow.status.in_(("PENDING", "RETRY")), EmailOutboxRow.purpose == "ANSWER",
                       EmailOutboxRow.conversation_id.in_(
                           select(ConversationRow.id).where(ConversationRow.email_verified_at.is_(None))))
                .values(status=AWAITING)
                .execution_options(synchronize_session=False)
            )
            ids = list(session.scalars(
                select(EmailOutboxRow.id)
                .where(EmailOutboxRow.status.in_(("PENDING", "RETRY")), EmailOutboxRow.next_attempt_at <= now)
                .order_by(EmailOutboxRow.created_at).limit(limit)
            ))
        sent = 0
        for row_id in ids:
            if self._deliver(row_id):
                sent += 1
        return sent

    def _deliver(self, row_id: uuid.UUID) -> bool:
        now = utcnow()
        with self.sessions() as session, session.begin():
            row = session.get(EmailOutboxRow, row_id)
            if row is None:
                return False
            conversation = session.get(ConversationRow, row.conversation_id)
            if row.purpose == "ANSWER" and (conversation is None or not conversation.email_verified_at):
                row.status = AWAITING           # out of the queue until verified (a notice queued before AWAITING)
                return False
            claimed = session.execute(
                update(EmailOutboxRow)
                .where(EmailOutboxRow.id == row_id, EmailOutboxRow.status.in_(("PENDING", "RETRY")),
                       EmailOutboxRow.next_attempt_at <= now)
                .values(status="SENDING", attempts=EmailOutboxRow.attempts + 1,
                        next_attempt_at=now + timedelta(minutes=5))
            )
            if claimed.rowcount != 1:
                return False
            session.refresh(row)
            try:
                values = (str(row.id), row.purpose, row.question_id, self._decrypt(row.recipient_ciphertext),
                          self._decrypt(row.token_ciphertext), row.attempts)
            except EmailNotConfigured:
                # the key changed or is missing: this message can never be sent; it leaves the queue for good
                row.status, row.last_error = "FAILED", "undecryptable"
                row.recipient_ciphertext = row.token_ciphertext = ""
                log.warning("email outbox id=%s cannot be decrypted; dropped", row.id)
                return False
        message_id, purpose, question_id, recipient, raw_token, attempt = values
        url = f"{self.settings.email_public_app_url.rstrip('/')}/employee#return_token={quote(raw_token)}"
        asked = ""
        if purpose == "VERIFY":
            subject = "אימות כתובת האימייל — יועץ הרכב של שטראוס"
            introduction = "כדי לאמת את כתובת האימייל ולחזור לשיחה, פתחו את הקישור המאובטח."
            button = "פתיחת השיחה המאובטחת"
        else:
            subject = "תשובה מנציג התחבורה — יועץ הרכב של שטראוס"
            introduction = "התקבלה תשובה מנציג התחבורה לפנייה שלכם. הקישור יפתח את השיחה ישירות בתשובה."
            button = "לצפייה בתשובת הנציג"
            asked = self._asked(row_id, question_id)      # the employee's own question (1/11), never the answer
        body = (f"{introduction}\n\n" + (f"השאלה שלכם: {asked}\n\n" if asked else "")
                + f"{button}: {url}\n\nהקישור תקף ל-24 שעות ופועל פעם אחת.")
        html_body = (
            '<div dir="rtl" style="font-family:Arial,sans-serif;max-width:560px;margin:auto;'
            'padding:24px;color:#18332c">'
            '<h1 style="font-size:22px">יועץ הרכב של שטראוס</h1>'
            f'<p style="line-height:1.7">{html.escape(introduction)}</p>'
            + (f'<p style="line-height:1.7;background:#f3f6f5;padding:12px 16px;border-radius:6px">'
               f'<strong>השאלה שלכם:</strong><br>{html.escape(asked)}</p>' if asked else "")
            + f'<p style="margin:28px 0"><a href="{html.escape(url, quote=True)}" '
            'style="background:#126b50;color:#fff;padding:12px 20px;border-radius:6px;'
            f'text-decoration:none;display:inline-block">{html.escape(button)}</a></p>'
            '<p style="font-size:13px;color:#52615c">הקישור תקף ל-24 שעות ופועל פעם אחת. '
            'אם לא ביקשתם זאת, אפשר להתעלם מההודעה.</p></div>'
        )
        try:
            self._send(message_id, recipient, subject, body, html_body)
        except Exception as exc:
            retry = now + timedelta(seconds=min(3600, 30 * (2 ** min(attempt - 1, 7))))
            with self.sessions() as session, session.begin():
                row = session.get(EmailOutboxRow, row_id)
                if row is not None:
                    row.status = "FAILED" if attempt >= MAX_ATTEMPTS else "RETRY"
                    row.last_error = type(exc).__name__[:80]
                    row.next_attempt_at = retry
            log.warning("email delivery failed id=%s attempt=%s error=%s", message_id, attempt, type(exc).__name__)
            return False
        with self.sessions() as session, session.begin():
            row = session.get(EmailOutboxRow, row_id)
            if row is not None and row.status == "SENDING":
                row.status = "SENT"
                row.last_error = None
                row.sent_at = utcnow()
                row.recipient_ciphertext = ""
                row.token_ciphertext = ""
        return True

    def _asked(self, row_id: uuid.UUID, question_id: str | None) -> str:
        """The question as the employee's screen shows it: the inquiry record's own lines in the conversation (never
        the representative's structured text), made safe for an email."""
        if not question_id:
            return ""
        with self.sessions() as session:
            row = session.get(EmailOutboxRow, row_id)
            if row is None:
                return ""
            content = session.scalar(
                select(MessageRow.content).where(MessageRow.conversation_id == row.conversation_id,
                                                 MessageRow.rep_question_id == question_id,
                                                 MessageRow.source == "rep_inquiry")
                .order_by(MessageRow.seq).limit(1))
        if not content:
            return ""
        head = content.split("\n\n")[0]
        return question_for_email("\n".join(head.split("\n")[1:]))

    def _send(self, message_id: str, recipient: str, subject: str, body: str, html_body: str) -> None:
        backend = self.settings.email_backend
        if backend == "disabled":
            raise EmailNotConfigured("Email backend is disabled")
        if backend == "fake":
            payload = {"to": recipient, "subject": subject, "body": body, "html": html_body}
            if not self.settings.email_fake_dir:
                self.fake_messages.append(payload)
                return
            path = Path(self.settings.email_fake_dir)
            path.mkdir(parents=True, exist_ok=True)
            (path / f"{message_id}.json").write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8")
            return
        message = EmailMessage()
        message["From"] = self.settings.email_from
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = f"<{message_id}@strauss-car-advisor>"
        message.set_content(body)
        message.add_alternative(html_body, subtype="html")
        with smtplib.SMTP(self.settings.email_smtp_host, self.settings.email_smtp_port, timeout=15) as smtp:
            if self.settings.email_smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if self.settings.email_smtp_username:
                smtp.login(self.settings.email_smtp_username, self.settings.email_smtp_password)
            smtp.send_message(message)


__all__ = ["EmailNotificationService", "EmailNotConfigured", "InvalidEmailLink"]
