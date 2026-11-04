"""Mail check on the VM (run inside the app container by vm-setup-and-test.sh). Sends nothing: the app's own
settings are loaded, the notifier is built as the app builds it, and the SMTP server is asked for a login only.
Prints PASS / FAIL / INFO lines with no address, password or link."""
import smtplib
import ssl
from email.utils import parseaddr

from app.modules.conversation.public import EmailNotificationService, EmailNotConfigured
from app.shared.settings import get_settings

s = get_settings()
if s.email_backend != "smtp":
    print(f"INFO  mail: EMAIL_BACKEND={s.email_backend} (no mail is sent; the app hides the email features)")
    raise SystemExit(0)
try:
    on = EmailNotificationService(None, s).enabled
except EmailNotConfigured as exc:
    print(f"FAIL  mail settings: {exc}")
    raise SystemExit(0)
print("PASS  mail settings (key, sender, public link)" if on else "FAIL  mail settings: notifier disabled")
domain = s.email_from.rsplit("@", 1)[-1].strip(" >")
print(f"INFO  mail sender domain: {domain}" + ("  (Resend test domain: delivers to the account owner only)"
                                             if domain.endswith("resend.dev") else ""))
if s.email_smtp_host == "smtp.gmail.com":  # Gmail rewrites any other From to the account itself
    same = parseaddr(s.email_from)[1].lower() == s.email_smtp_username.strip().lower()
    print("PASS  Gmail: sender is the signed-in account" if same else "FAIL  Gmail: EMAIL_FROM is not the SMTP account")
print("PASS  public link is https" if s.email_public_app_url.startswith("https://") else "FAIL  public link not https")
try:
    with smtplib.SMTP(s.email_smtp_host, s.email_smtp_port, timeout=15) as smtp:
        if s.email_smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        smtp.login(s.email_smtp_username, s.email_smtp_password)
    print("PASS  SMTP login (nothing sent)")
except Exception as exc:  # the class only: an SMTP reply could echo the user name
    print(f"FAIL  SMTP login: {type(exc).__name__}")
