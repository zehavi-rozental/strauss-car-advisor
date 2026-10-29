"""Encrypted email addresses, one-time conversation links, and a retryable notification outbox."""
from __future__ import annotations

import hashlib
import html
import json
import logging
import secrets
import smtplib
import ssl
import uuid
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker

from app.shared.db import utcnow

from .models import ConversationRow, EmailLinkRow, EmailOutboxRow
from .repository import token_hash

log = logging.getLogger(__name__)

EMAIL_RE = r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
MAX_ATTEMPTS = 8
LINK_TTL = timedelta(hours=24)


class EmailNotConfigured(Exception):
    pass


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
                             question_id: str | None = None) -> None:
        email = self.normalize_email(email)
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        if conversation.email_verified_at:
            if not conversation.email_ciphertext or self._decrypt(conversation.email_ciphertext) != email:
                raise ValueError("כתובת האימייל כבר אומתה ואי אפשר להחליף אותה דרך הפנייה.")
            return
        conversation.email_ciphertext = self._encrypt(email)
        dedupe_key = f"verify:{conversation.id}:{digest}"
        if session.scalar(select(EmailOutboxRow.id).where(EmailOutboxRow.dedupe_key == dedupe_key)):
            return
        self._queue(session, conversation, email, "VERIFY", question_id, dedupe_key, digest)

    def prepare_answer(self, session, conversation: ConversationRow, question_id: str) -> None:
        if not conversation.email_ciphertext:
            return
        email = self._decrypt(conversation.email_ciphertext)
        digest = hashlib.sha256(email.encode("utf-8")).hexdigest()
        dedupe_key = f"answer:{question_id}"
        if session.scalar(select(EmailOutboxRow.id).where(EmailOutboxRow.dedupe_key == dedupe_key)):
            return
        self._queue(session, conversation, email, "ANSWER", question_id, dedupe_key, digest)

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
               question_id: str | None, dedupe_key: str, email_hash: str) -> None:
        raw_token = secrets.token_urlsafe(32)
        session.add(EmailLinkRow(
            conversation_id=conversation.id, question_id=question_id, purpose=purpose,
            token_hash=token_hash(raw_token), email_hash=email_hash, expires_at=utcnow() + LINK_TTL))
        session.add(EmailOutboxRow(
            dedupe_key=dedupe_key, conversation_id=conversation.id, question_id=question_id, purpose=purpose,
            recipient_ciphertext=self._encrypt(email), token_ciphertext=self._encrypt(raw_token),
            status="PENDING", attempts=0, next_attempt_at=utcnow()))

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
            values = (str(row.id), row.purpose, row.question_id, self._decrypt(row.recipient_ciphertext),
                      self._decrypt(row.token_ciphertext), row.attempts)
        message_id, purpose, question_id, recipient, raw_token, attempt = values
        url = f"{self.settings.email_public_app_url.rstrip('/')}/employee#return_token={quote(raw_token)}"
        if purpose == "VERIFY":
            subject = "אימות כתובת האימייל — יועץ הרכב של שטראוס"
            introduction = "כדי לאמת את כתובת האימייל ולחזור לשיחה, פתחו את הקישור המאובטח."
        else:
            subject = "תשובה מנציג התחבורה — יועץ הרכב של שטראוס"
            introduction = "נציג התחבורה ענה לפנייה שלכם. פתחו את הקישור המאובטח כדי לראות את התשובה ולהמשיך בשיחה."
        body = f"{introduction}\n\n{url}\n\nהקישור תקף ל-24 שעות ופועל פעם אחת."
        html_body = (
            '<div dir="rtl" style="font-family:Arial,sans-serif;max-width:560px;margin:auto;'
            'padding:24px;color:#18332c">'
            '<h1 style="font-size:22px">יועץ הרכב של שטראוס</h1>'
            f'<p style="line-height:1.7">{html.escape(introduction)}</p>'
            f'<p style="margin:28px 0"><a href="{html.escape(url, quote=True)}" '
            'style="background:#126b50;color:#fff;padding:12px 20px;border-radius:6px;'
            'text-decoration:none;display:inline-block">פתיחת השיחה המאובטחת</a></p>'
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
