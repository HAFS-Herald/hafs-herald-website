# backend/mailer.py — sending email through Resend's HTTP API.
#
# Railway blocks outbound SMTP on Trial/Hobby plans, so this uses HTTPS.
# Configure on Railway:
#   RESEND_API_KEY = re_...                                   (required to send anything)
#   MAIL_FROM      = The HAFS Herald <newsletter@hafsherald.com>   (newsletter)
#   NOTIFY_FROM    = Herald Newsroom <newsroom@hafsherald.com>     (staff notifications; defaults to MAIL_FROM)
#   MAIL_REPLY_TO  = contact@hafsherald.com                   (optional: where replies go anyway)
# Without RESEND_API_KEY every send is skipped, so the site works the same.
from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

API_URL = "https://api.resend.com"
BATCH_SIZE = 100  # Resend's limit per batch request


class MailError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status

    @property
    def is_rate_limit(self) -> bool:
        # 429: too many requests, or the plan's daily/monthly quota is used up
        return self.status == 429


def is_configured() -> bool:
    return bool(os.environ.get("RESEND_API_KEY"))


def sender() -> str:
    return os.environ.get("MAIL_FROM") or "The HAFS Herald <newsletter@hafsherald.com>"


def notify_sender() -> str:
    """Address for internal staff emails, kept separate from the newsletter's."""
    return os.environ.get("NOTIFY_FROM") or sender()


def _message(to: str, subject: str, html: str, text: str, headers: Optional[Dict[str, str]] = None,
             from_addr: Optional[str] = None) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"from": from_addr or sender(), "to": [to], "subject": subject, "html": html, "text": text}
    reply_to = os.environ.get("MAIL_REPLY_TO")
    if reply_to:
        msg["reply_to"] = reply_to
    if headers:
        msg["headers"] = headers
    return msg


def _post(path: str, payload: Any) -> Any:
    req = urllib.request.Request(
        API_URL + path,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {os.environ.get('RESEND_API_KEY', '')}",
            "Content-Type": "application/json",
            "User-Agent": "hafs-herald-cms",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        raise MailError(f"Resend returned {e.code}: {detail}", e.code) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise MailError(f"Couldn't reach Resend: {e}") from e


def send(to: str, subject: str, html: str, text: str, headers: Optional[Dict[str, str]] = None,
         from_addr: Optional[str] = None) -> None:
    """Send one email. Raises MailError on failure."""
    if not is_configured():
        raise MailError("Email isn't set up (RESEND_API_KEY is missing).")
    _post("/emails", _message(to, subject, html, text, headers, from_addr))


def send_batch(messages: List[Dict[str, Any]]) -> None:
    """Send up to BATCH_SIZE prepared messages (dicts from message()) in one request."""
    if not is_configured():
        raise MailError("Email isn't set up (RESEND_API_KEY is missing).")
    if messages:
        _post("/emails/batch", messages)


def message(to: str, subject: str, html: str, text: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    return _message(to, subject, html, text, headers)


def send_in_background(to: str, subject: str, html: str, text: str) -> None:
    """Fire-and-forget for staff notifications: a slow or failed send never blocks a page."""
    if not is_configured():
        return

    def run():
        try:
            send(to, subject, html, text, from_addr=notify_sender())
        except MailError as e:
            print(f"[mail] notification to {to} failed: {e}", flush=True)

    threading.Thread(target=run, daemon=True).start()
