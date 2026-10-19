# app.py — The HAFS Herald CMS (Flask + SQLite)
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import difflib
import secrets
import sqlite3
import tempfile
import threading
import time
import unicodedata
import zipfile
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask_cors import CORS
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import mailer

try:
    from PIL import Image, ImageOps
except ImportError:  # uploads still work, just without resizing
    Image = None

from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    session,
    url_for,
)

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"

# Persist data on Railway by mounting a Volume and (optionally) setting these env vars:
#   DATA_DIR=/data
#   DB_PATH=/data/data.sqlite3
#   UPLOAD_DIR=/data/uploads
#
# If you forget to set DATA_DIR on Railway but you *did* mount a Volume at /data,
# this will auto-detect /data and use it.
def _detect_data_dir() -> Path:
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)

    candidate = Path("/data")
    if candidate.exists() and candidate.is_dir():
        try:
            probe = candidate / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            pass

    return BASE_DIR


DATA_DIR = _detect_data_dir()
DB_PATH = Path(os.environ.get("DB_PATH") or str(DATA_DIR / "data.sqlite3"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR") or str(DATA_DIR / "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR") or str(DATA_DIR / "backups"))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_KEEP = int(os.environ.get("BACKUP_KEEP", "14"))

# draft: writer is working on it; review: ready for an editor; published: live on the site
STATUSES = ("draft", "review", "published")
STATUS_LABELS = {"draft": "Draft", "review": "In review", "published": "Published"}

# writer: own articles, can't publish; editor: all articles, publishing, tips, settings;
# admin: everything, including subscribers, backups and user accounts
ROLES = ("writer", "editor", "admin")
ROLE_LABELS = {"writer": "Writer", "editor": "Editor", "admin": "Admin"}
BUILTIN_ADMIN = "admin"  # signs in with HAFS_ADMIN_PASSWORD; always available as a recovery login
MIN_PASSWORD_LEN = 8

# Public form limits: (max submissions, per seconds) per visitor
RATE_LIMITS = {"tip": (5, 3600), "subscribe": (5, 3600), "login": (10, 900)}
MAX_TIP_CHARS = 5000
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE_PX = int(os.environ.get("MAX_IMAGE_PX", "1600"))
# Where people can actually write to us; named in the "no reply" note on automated emails
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL") or "contact@hafsherald.com"
NO_REPLY_TEXT = (f"This is an automated message from a send-only address, so replies aren't read. "
                 f"To reach us, write to {CONTACT_EMAIL}.")
# The public site (Cloudflare Pages). Set PUBLIC_SITE_URL if it moves to a custom domain.
PUBLIC_SITE_URL = (os.environ.get("PUBLIC_SITE_URL") or "https://hafsherald.com").rstrip("/")

SECTIONS = [
    "News",
    "Opinion",
    "Science",
    "Economics",
    "History",
    "Policy / Public Affairs",
    "Math & Data",
    "Sports",
    "Student Life",
    "Arts & Culture",
]

DEFAULT_SETTINGS = {
    "currentIssueNumber": 1,
    "currentIssueDate": datetime.now().strftime("%Y-%m-%d"),
    "staff": [
        {"role": "Editor-in-Chief", "name": "Name Here", "contact": ""},
        {"role": "Managing Editor", "name": "Name Here", "contact": ""},
        {"role": "Design", "name": "Name Here", "contact": ""},
    ],
}


def load_secret_key() -> str:
    """Use FLASK_SECRET if set; otherwise keep a generated key in DATA_DIR.

    Gunicorn runs several workers, so a per-process random key would make
    logins and CSRF tokens fail whenever a request hits a different worker.
    """
    env = os.environ.get("FLASK_SECRET")
    if env:
        return env
    key_file = DATA_DIR / ".flask_secret"
    try:
        return key_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        key = secrets.token_hex(32)
        try:
            # O_EXCL so concurrent workers can't each write a different key
            fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(key)
            return key
        except FileExistsError:
            return key_file.read_text(encoding="utf-8").strip()


def admin_password() -> str:
    return os.environ.get("HAFS_ADMIN_PASSWORD") or os.environ.get("ADMIN_PASSWORD") or ""


def safe_next(target: str) -> str:
    # Only allow redirects to paths on this site (blocks //evil.com and https://...)
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return "/admin"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def safe_filename(name: str) -> str:
    name = name.strip().replace("\\", "/").split("/")[-1]
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
    return name or "upload"


def clean_author(author: str) -> str:
    """Bylines get "By" added by the site, so drop one typed into the Author field ("by Kim" -> "Kim")."""
    return re.sub(r"^\s*by\b[\s:]*", "", author or "", flags=re.IGNORECASE).strip()


def save_upload_image(f, target_dir: Path) -> str:
    """Save an uploaded image resized for the web and return its filename.

    Phone photos are 3–10 MB; this shrinks them to MAX_IMAGE_PX on the long
    side, fixes rotation, and drops EXIF data (which can include GPS location).
    Raises ValueError if the file isn't a readable image.
    """
    original = safe_filename(f.filename)
    stem, ext = Path(original).stem, Path(original).suffix.lower()
    token = secrets.token_hex(6)
    if ext not in ALLOWED_EXTS:
        raise ValueError("not an allowed image type")

    if Image is None or ext == ".gif":
        # GIFs may be animated; keep them as they are
        name = f"{stem}_{token}{ext}"
        f.save(target_dir / name)
        return name

    try:
        im = Image.open(f.stream)
        im = ImageOps.exif_transpose(im)
    except Exception as e:
        raise ValueError("not a readable image") from e

    im.thumbnail((MAX_IMAGE_PX, MAX_IMAGE_PX))
    has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
    if ext == ".png" and has_alpha:
        out_ext, fmt, opts = ".png", "PNG", {"optimize": True}
        im = im.convert("RGBA")
    elif ext == ".webp":
        out_ext, fmt, opts = ".webp", "WEBP", {"quality": 82}
        im = im.convert("RGBA" if has_alpha else "RGB")
    else:
        # Photos saved as PNG get much smaller as JPEG
        out_ext, fmt, opts = ".jpg", "JPEG", {"quality": 82, "optimize": True, "progressive": True}
        im = im.convert("RGB")

    name = f"{stem}_{token}{out_ext}"
    im.save(target_dir / name, fmt, **opts)
    return name


# Revised Romanization pieces for Hangul syllables (initial, vowel, final), so Korean titles get readable links
HANGUL_INITIALS = ["g", "kk", "n", "d", "tt", "r", "m", "b", "pp", "s", "ss", "", "j", "jj", "ch", "k", "t", "p", "h"]
HANGUL_VOWELS = ["a", "ae", "ya", "yae", "eo", "e", "yeo", "ye", "o", "wa", "wae", "oe", "yo", "u", "wo", "we", "wi",
                 "yu", "eu", "ui", "i"]
HANGUL_FINALS = ["", "k", "k", "k", "n", "n", "n", "t", "l", "k", "m", "l", "l", "l", "p", "l", "m", "p", "p", "t",
                 "t", "ng", "t", "t", "k", "t", "p", "t"]
MAX_SLUG_LEN = 60
STAFF_BYLINE = "The HAFS Herald Staff"  # byline for articles made with the built-in admin login


def romanize_hangul(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(HANGUL_INITIALS[code // 588] + HANGUL_VOWELS[(code % 588) // 28] + HANGUL_FINALS[code % 28])
        else:
            out.append(ch)
    return "".join(out)


def make_slug(text: str) -> str:
    """An article's link ID from its title: "도서관 시간 연장" -> "doseogwan-sigan-yeonjang".

    Mirrors makeSlug() in backend/templates/admin_edit.html, which previews it while typing.
    """
    s = unicodedata.normalize("NFKD", romanize_hangul(text or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > MAX_SLUG_LEN:
        cut = s[:MAX_SLUG_LEN]
        s = cut[:cut.rfind("-")] if "-" in cut else cut
    return s.strip("-")


IMAGE_BLOCK_RE = re.compile(r'^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]+)")?\)$')
NUMBERED_RE = re.compile(r"^(\d{1,3})[.)] ")
LINK_RE = re.compile(r"\[([^\]]+)\]\(((?:https?://|mailto:|/)[^\s)]*)\)")
BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
ITALIC_RE = re.compile(r"(?<![*\w])\*(?![\s*])(.+?)(?<![\s*])\*(?![*\w])")


def format_inline(text: str) -> str:
    """**bold**, *italic* and [links](https://...) on already-escaped text.

    Mirrors formatInline() in frontend/public/common.js.
    """
    s = str(escape(text))
    s = LINK_RE.sub(r'<a href="\2" target="_blank" rel="noopener">\1</a>', s)
    s = BOLD_RE.sub(r"<strong>\1</strong>", s)
    return ITALIC_RE.sub(r"<em>\1</em>", s)


def render_body_block(p: str) -> Markup:
    """One body paragraph to HTML. Mirrors renderBlock() in frontend/public/article.js."""
    s = (p or "").strip()
    m = IMAGE_BLOCK_RE.match(s)
    if m:
        src = m.group(2)
        if not re.match(r"^(https?:)?//", src) and not src.startswith("/"):
            src = "/" + src
        cap = f'<figcaption class="muted small">{escape(m.group(3))}</figcaption>' if m.group(3) else ""
        return Markup(
            f'<figure class="inline-media"><img src="{escape(src)}" alt="{escape(m.group(1))}" loading="lazy" />{cap}</figure>'
        )
    if s.startswith("## "):
        return Markup(f"<h2>{format_inline(s[3:])}</h2>")
    lines = s.split("\n")
    if all(re.match(r"^[-*] ", ln) for ln in lines):
        return Markup("<ul>" + "".join(f"<li>{format_inline(ln[2:])}</li>" for ln in lines) + "</ul>")
    if all(NUMBERED_RE.match(ln) for ln in lines):
        first = int(NUMBERED_RE.match(lines[0]).group(1))
        start = f' start="{first}"' if first != 1 else ""
        items = "".join(f"<li>{format_inline(NUMBERED_RE.sub('', ln, count=1))}</li>" for ln in lines)
        return Markup(f"<ol{start}>{items}</ol>")
    return Markup(f"<p>{format_inline(s)}</p>")


def password_fingerprint(material: str) -> str:
    """Short keyed hash stored in the session; changing a password signs out its other sessions."""
    return hmac.new(str(current_app.secret_key).encode(), material.encode(), hashlib.sha256).hexdigest()[:16]


def get_user(username: str) -> Optional[sqlite3.Row]:
    with conn() as c:
        return c.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def list_users():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM users ORDER BY active DESC, role DESC, display_name").fetchall()]


def load_session_user():
    """Resolve the signed-in user on every request, so role changes and deactivation apply at once."""
    g.user = None
    username = session.get("user")
    if not username:
        return
    if username == BUILTIN_ADMIN:
        pw = admin_password()
        if pw and session.get("pwv") == password_fingerprint(pw):
            g.user = {"username": BUILTIN_ADMIN, "name": "Admin", "role": "admin", "builtin": True}
    else:
        r = get_user(username)
        if r and r["active"] and session.get("pwv") == password_fingerprint(r["password_hash"]):
            g.user = {"username": r["username"], "name": r["display_name"], "role": r["role"], "builtin": False}
    if g.user is None:
        session.pop("user", None)
        session.pop("pwv", None)


NOTIFY_THROTTLE_SECONDS = 30 * 60  # at most one email per recipient per topic in this window


def staff_recipients(roles=(), usernames=(), exclude=()) -> List[Dict[str, str]]:
    """Active staff with an email address and notifications on."""
    with conn() as c:
        rows = c.execute("""SELECT username, display_name, role, email, notify_mode FROM users
                            WHERE active = 1 AND notify_mode != 'off' AND email != ''""").fetchall()
    return [dict(r) for r in rows
            if (r["role"] in roles or r["username"] in usernames) and r["username"] not in exclude]


NOTIFY_MODES = {"daily": "Daily summary", "instant": "Right away", "off": "Off"}
# The daily summary goes out at this hour, Korea time (UTC+9, no daylight saving)
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "18"))
KST = timezone(timedelta(hours=9))


def notify(recipients: List[Dict[str, str]], topic: str, subject: str, heading: str, lines: List[str],
           link: str, link_label: str, event: Dict[str, str]):
    """Tell staff about something: queued for their daily summary, or emailed right away if they chose that.

    Immediate emails skip anyone already emailed about the same topic recently.
    """
    if not mailer.is_configured() or not recipients:
        return
    now = time.time()
    with conn() as c:
        c.execute("DELETE FROM notify_log WHERE ts < ?", (now - 86400,))
        daily = [r for r in recipients if r.get("notify_mode") == "daily"]
        c.executemany(
            """INSERT INTO notify_queue (recipient, kind, article_id, title, actor, detail, link, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            [(r["username"], event["kind"], event.get("article_id", ""), event.get("title", ""),
              event.get("actor", ""), event.get("detail", "")[:500], link, utc_now_iso()) for r in daily])
        for r in recipients:
            if r.get("notify_mode") != "instant":
                continue
            recent = c.execute("SELECT 1 FROM notify_log WHERE recipient = ? AND key = ? AND ts > ?",
                               (r["username"], topic, now - NOTIFY_THROTTLE_SECONDS)).fetchone()
            if recent:
                continue
            c.execute("INSERT INTO notify_log (recipient, key, ts) VALUES (?,?,?)", (r["username"], topic, now))
            html = render_template("email_notification.html", heading=heading, lines=lines, link=link,
                                   link_label=link_label, name=r["display_name"])
            text = "\n\n".join([f"Hi {r['display_name']},", heading, *lines, f"{link_label}: {link}",
                                 "You can turn these emails off under Account in the newsroom.", NO_REPLY_TEXT])
            mailer.send_in_background(r["email"], subject, html, text)
        c.commit()


def admin_url(path: str) -> str:
    return request.url_root.rstrip("/") + path


def require_role(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if not g.get("user"):
                return redirect(url_for("admin_login", next=request.path))
            if roles and g.user["role"] not in roles:
                abort(403)
            return fn(*args, **kwargs)
        return wrapper
    return deco


require_login = require_role()
require_editor = require_role("editor", "admin")
require_admin = require_role("admin")


def can_edit_article(user: Dict[str, Any], a: Dict[str, Any]) -> bool:
    if user["role"] in ("editor", "admin"):
        return True
    # Writers: their own articles, until an editor publishes them
    return a.get("created_by") == user["username"] and a.get("status") != "published"


def csrf_token() -> str:
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf"] = tok
    return tok


def verify_csrf() -> bool:
    form_tok = request.form.get("csrf", "")
    return bool(form_tok) and form_tok == session.get("csrf", "")


def row_to_article(r: sqlite3.Row) -> Dict[str, Any]:
    body = []
    try:
        body = json.loads(r["body_json"]) if r["body_json"] else []
        if not isinstance(body, list):
            body = []
    except Exception:
        body = []
    return {
        "id": r["id"],
        "title": r["title"],
        "section": r["section"],
        "author": r["author"],
        "date": r["date"],
        "summary": r["summary"],
        "cover": r["cover"],
        "issue": int(r["issue"]) if "issue" in r.keys() else 0,
        "pullQuote": r["pullQuote"],
        "body": body,
        "status": r["status"] if "status" in r.keys() else "published",
        "correction": r["correction"] if "correction" in r.keys() else "",
        "lead": bool(r["featured"]),
        "created_by": r["created_by"] if "created_by" in r.keys() else "",
        "updated_by": r["updated_by"] if "updated_by" in r.keys() else "",
        "updated_at": r["updated_at"],
    }


PUBLIC_ARTICLE_FIELDS = ("id", "title", "section", "author", "date", "summary", "cover", "issue", "pullQuote", "body", "correction", "lead", "updated_at")


def public_article(a: Dict[str, Any]) -> Dict[str, Any]:
    # Keep staff usernames and workflow status out of the public API
    return {k: a[k] for k in PUBLIC_ARTICLE_FIELDS}


def init_db():
    with conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS articles (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                section TEXT NOT NULL,
                author TEXT NOT NULL,
                date TEXT NOT NULL,
                summary TEXT NOT NULL,
                cover TEXT NOT NULL,
                featured INTEGER NOT NULL,
                issue INTEGER NOT NULL DEFAULT 0,
                pullQuote TEXT NOT NULL,
                body_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'published',
                correction TEXT NOT NULL DEFAULT '',
                created_by TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL DEFAULT ''
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT NOT NULL,
                block INTEGER NOT NULL,          -- body paragraph index; -1 = whole article
                quote TEXT NOT NULL,             -- paragraph text when commented, to re-anchor after edits
                body TEXT NOT NULL,
                author TEXT NOT NULL,            -- username
                author_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS comments_article ON comments (article_id)")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS revisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id TEXT NOT NULL,
                saved_at TEXT NOT NULL,
                saved_by TEXT NOT NULL,          -- display name
                status TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,     -- the article's editable fields after this save
                note TEXT NOT NULL DEFAULT ''
            )
            """
        )
        c.execute("CREATE INDEX IF NOT EXISTS revisions_article ON revisions (article_id, id)")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS notify_log (
                recipient TEXT NOT NULL,
                key TEXT NOT NULL,
                ts REAL NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS newsletter_sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue INTEGER NOT NULL,
                subject TEXT NOT NULL,
                intro TEXT NOT NULL,
                started_at TEXT NOT NULL,
                started_by TEXT NOT NULL,
                total INTEGER NOT NULL,
                finished INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT ''
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS newsletter_recipients (
                send_id INTEGER NOT NULL,
                subscriber_id INTEGER NOT NULL,
                PRIMARY KEY (send_id, subscriber_id)
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                notify INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS notify_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recipient TEXT NOT NULL,         -- username
                kind TEXT NOT NULL,              -- review | comment | published | tip
                article_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                actor TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                sent INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        c.execute("CREATE TABLE IF NOT EXISTS digest_runs (day TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
        user_cols = [row["name"] for row in c.execute("PRAGMA table_info(users)").fetchall()]
        if "email" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        if "notify" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN notify INTEGER NOT NULL DEFAULT 1")
        if "notify_mode" not in user_cols:
            # off | instant | daily. Everyone moves to the daily summary; people who had emails off stay off.
            c.execute("ALTER TABLE users ADD COLUMN notify_mode TEXT NOT NULL DEFAULT 'daily'")
            c.execute("UPDATE users SET notify_mode = 'off' WHERE notify = 0")
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
            """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS tips (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new'
            )
            """
        )

        c.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_events (
                visitor TEXT NOT NULL,
                kind TEXT NOT NULL,
                ts REAL NOT NULL
            )
            """
        )

        # Each subscriber gets a secret token for their unsubscribe link
        sub_cols = [row["name"] for row in c.execute("PRAGMA table_info(subscribers)").fetchall()]
        if "token" not in sub_cols:
            c.execute("ALTER TABLE subscribers ADD COLUMN token TEXT NOT NULL DEFAULT ''")
        for row in c.execute("SELECT id FROM subscribers WHERE token = ''").fetchall():
            c.execute("UPDATE subscribers SET token = ? WHERE id = ?", (secrets.token_urlsafe(24), row["id"]))

        # Migration: ensure 'issue' exists even if DB created earlier
        cols = [row["name"] for row in c.execute("PRAGMA table_info(articles)").fetchall()]
        if "issue" not in cols:
            c.execute("ALTER TABLE articles ADD COLUMN issue INTEGER NOT NULL DEFAULT 0")
        # Migration: articles saved before drafts existed were all live, so they stay published
        if "status" not in cols:
            c.execute("ALTER TABLE articles ADD COLUMN status TEXT NOT NULL DEFAULT 'published'")
        if "correction" not in cols:
            c.execute("ALTER TABLE articles ADD COLUMN correction TEXT NOT NULL DEFAULT ''")
        # One-time cleanup: bylines saved as "By Kim" showed as "By By Kim"
        for row in c.execute("SELECT id, author FROM articles WHERE author LIKE 'by%' COLLATE NOCASE").fetchall():
            if clean_author(row["author"]):
                c.execute("UPDATE articles SET author = ? WHERE id = ?", (clean_author(row["author"]), row["id"]))
        for col in ("created_by", "updated_by"):
            if col not in cols:
                c.execute(f"ALTER TABLE articles ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")

        c.commit()

    seed_settings_if_missing()
    seed_articles_if_empty()


def seed_articles_if_empty():
    with conn() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
        if n == 0:
            now = datetime.now().strftime("%Y-%m-%d")
            demo = {
                "id": "welcome",
                "title": "Welcome to The HAFS Herald",
                "section": "News",
                "author": "The HAFS Herald Staff",
                "date": now,
                "summary": "Discimus Scribendo — the new home for student journalism at HAFS.",
                "cover": "",
                "issue": 1,
                "pullQuote": "We don’t chase certainty; we chase clarity.",
                "status": "published",
                "body": [
                    "This is a seeded demo article. Replace it in /admin, or delete it.",
                    "Use the Issue number field to decide what appears on the homepage.",
                ],
            }
            upsert_article(demo, connection=c)
            c.commit()


def seed_settings_if_missing():
    with conn() as c:
        r = c.execute("SELECT COUNT(*) AS n FROM settings").fetchone()
        if r["n"] == 0:
            set_settings(DEFAULT_SETTINGS, connection=c)
            c.commit()


def get_settings() -> Dict[str, Any]:
    with conn() as c:
        r = c.execute("SELECT value_json FROM settings WHERE key = ?", ("site",)).fetchone()
        if not r:
            seed_settings_if_missing()
            r = c.execute("SELECT value_json FROM settings WHERE key = ?", ("site",)).fetchone()
        return json.loads(r["value_json"]) if r else {}


def set_settings(settings_obj: Dict[str, Any], connection: Optional[sqlite3.Connection] = None):
    owns = False
    if connection is None:
        connection = conn()
        owns = True
    now = utc_now_iso()
    connection.execute(
        """
        INSERT INTO settings (key, value_json, updated_at)
        VALUES (?,?,?)
        ON CONFLICT(key) DO UPDATE SET
          value_json=excluded.value_json,
          updated_at=excluded.updated_at
        """,
        ("site", json.dumps(settings_obj, ensure_ascii=False), now),
    )
    if owns:
        connection.commit()
        connection.close()


def upsert_article(a: Dict[str, Any], connection: Optional[sqlite3.Connection] = None):
    owns = False
    if connection is None:
        connection = conn()
        owns = True

    now = utc_now_iso()

    issue_val = int(a.get("issue") or 0)
    status = a.get("status") if a.get("status") in STATUSES else "draft"
    body_list = a.get("body", [])
    if not isinstance(body_list, list):
        body_list = []

    connection.execute(
        """
        INSERT INTO articles (id,title,section,author,date,summary,cover,featured,issue,pullQuote,body_json,updated_at,status,correction,created_by,updated_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          title=excluded.title,
          section=excluded.section,
          author=excluded.author,
          date=excluded.date,
          summary=excluded.summary,
          cover=excluded.cover,
          featured=excluded.featured,
          issue=excluded.issue,
          pullQuote=excluded.pullQuote,
          body_json=excluded.body_json,
          updated_at=excluded.updated_at,
          status=excluded.status,
          correction=excluded.correction,
          updated_by=excluded.updated_by
        """,
        (
            (a.get("id") or "").strip(),
            (a.get("title") or "").strip(),
            (a.get("section") or "").strip(),
            (a.get("author") or "").strip(),
            (a.get("date") or "").strip(),
            (a.get("summary") or "").strip(),
            (a.get("cover") or "").strip(),
            1 if a.get("lead") else 0,
            issue_val,
            (a.get("pullQuote") or "").strip(),
            json.dumps(body_list, ensure_ascii=False),
            now,
            status,
            (a.get("correction") or "").strip(),
            a.get("created_by") or "",
            a.get("updated_by") or "",
        ),
    )

    if owns:
        connection.commit()
        connection.close()


def delete_article(article_id: str):
    with conn() as c:
        c.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        c.execute("DELETE FROM comments WHERE article_id = ?", (article_id,))
        c.execute("DELETE FROM revisions WHERE article_id = ?", (article_id,))
        c.commit()


MAX_REVISIONS = int(os.environ.get("MAX_REVISIONS", "50"))
REVISION_FIELDS = ("title", "section", "author", "date", "summary", "cover", "issue", "pullQuote", "body", "correction")
REVISION_LABELS = {"title": "Title", "section": "Section", "author": "Author", "date": "Date", "summary": "Summary",
                   "cover": "Cover image", "issue": "Issue", "pullQuote": "Pull quote", "correction": "Correction"}


def record_revision(a: Dict[str, Any], saved_by: str, note: str = ""):
    """Snapshot an article after a save. Skipped when nothing in the text changed."""
    snap = {k: a.get(k) for k in REVISION_FIELDS}
    with conn() as c:
        last = c.execute("SELECT snapshot_json, status FROM revisions WHERE article_id = ? ORDER BY id DESC LIMIT 1",
                         (a["id"],)).fetchone()
        if last and json.loads(last["snapshot_json"]) == snap and last["status"] == a["status"] and not note:
            return
        c.execute(
            "INSERT INTO revisions (article_id, saved_at, saved_by, status, snapshot_json, note) VALUES (?,?,?,?,?,?)",
            (a["id"], utc_now_iso(), saved_by, a["status"], json.dumps(snap, ensure_ascii=False), note),
        )
        # Keep the newest MAX_REVISIONS per article
        c.execute(
            """DELETE FROM revisions WHERE article_id = ? AND id NOT IN
               (SELECT id FROM revisions WHERE article_id = ? ORDER BY id DESC LIMIT ?)""",
            (a["id"], a["id"], MAX_REVISIONS),
        )
        c.commit()


def word_diff(old: str, new: str) -> Markup:
    """Inline word-level diff: removed words in <del>, added words in <ins>."""
    # Words, spaces and punctuation as separate tokens, so "**10" -> "**11" shows only the number changing
    tokens = re.compile(r"\w+|\s+|[^\w\s]")
    a, b = tokens.findall(old or ""), tokens.findall(new or "")
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(str(escape("".join(a[i1:i2]))))
            continue
        if i2 > i1:
            out.append(f"<del>{escape(''.join(a[i1:i2]))}</del>")
        if j2 > j1:
            out.append(f"<ins>{escape(''.join(b[j1:j2]))}</ins>")
    return Markup("".join(out))


def diff_articles(old: Dict[str, Any], new: Dict[str, Any]):
    """What changed between two snapshots: field changes, then body paragraphs."""
    fields = []
    for k in REVISION_FIELDS:
        if k == "body":
            continue
        o, n = str(old.get(k) or ""), str(new.get(k) or "")
        if o != n:
            fields.append({"label": REVISION_LABELS[k], "html": word_diff(o, n)})
    ob, nb = old.get("body") or [], new.get("body") or []
    paras = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ob, nb, autojunk=False).get_opcodes():
        if op == "equal":
            paras.extend({"kind": "same", "html": escape(p)} for p in nb[j1:j2])
        elif op == "replace" and (i2 - i1) == (j2 - j1):
            # Same number of paragraphs: show each one's edits inline
            paras.extend({"kind": "changed", "html": word_diff(o, n)} for o, n in zip(ob[i1:i2], nb[j1:j2]))
        else:
            paras.extend({"kind": "removed", "html": escape(p)} for p in ob[i1:i2])
            paras.extend({"kind": "added", "html": escape(p)} for p in nb[j1:j2])
    return fields, paras


MAX_COMMENT_CHARS = 2000


def anchor_comments(body, comments):
    """Attach comments to the paragraphs they were written on.

    A comment stays on its paragraph while that paragraph's text is unchanged
    (even if paragraphs were added above it). Comments whose paragraph was
    rewritten or removed come back as `orphaned`, shown with the old text.
    """
    by_block = {i: [] for i in range(len(body))}
    general, orphaned = [], []
    for cm in comments:
        if cm["block"] < 0:
            general.append(cm)
            continue
        if cm["block"] < len(body) and body[cm["block"]] == cm["quote"]:
            by_block[cm["block"]].append(cm)
            continue
        moved = [i for i, p in enumerate(body) if p == cm["quote"]]
        if moved:
            # Closest matching paragraph, in case the same text appears twice
            by_block[min(moved, key=lambda i: abs(i - cm["block"]))].append(cm)
        else:
            orphaned.append(cm)
    return by_block, general, orphaned


def is_valid_email(email: str) -> bool:
    email = (email or "").strip()
    if len(email) > 254:
        return False
    # Simple sanity check; not RFC-perfect
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def add_subscriber(email: str) -> bool:
    email = (email or "").strip().lower()
    if not is_valid_email(email):
        return False
    now = utc_now_iso()
    with conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO subscribers (email, created_at, token) VALUES (?, ?, ?)",
            (email, now, secrets.token_urlsafe(24)),
        )
        c.commit()
    return True


def add_tip(message: str) -> bool:
    message = (message or "").strip()
    if len(message) < 5 or len(message) > MAX_TIP_CHARS:
        return False
    now = utc_now_iso()
    with conn() as c:
        c.execute(
            "INSERT INTO tips (message, created_at, status) VALUES (?, ?, 'new')",
            (message, now),
        )
        c.commit()
    return True


def backup_path(name: str) -> Optional[Path]:
    """Resolve a backup filename from a form, refusing anything outside BACKUP_DIR."""
    if not re.match(r"^data-[0-9A-Za-z_-]+\.sqlite3$", name or ""):
        return None
    p = BACKUP_DIR / name
    return p if p.is_file() else None


def make_backup(label: str) -> Path:
    """Copy the live database into BACKUP_DIR using SQLite's online backup."""
    out = BACKUP_DIR / f"data-{label}.sqlite3"
    tmp = out.with_suffix(".partial")
    with conn() as src:
        dst = sqlite3.connect(tmp)
        try:
            src.backup(dst)
        finally:
            dst.close()
    os.replace(tmp, out)
    return out


def list_backups():
    items = []
    for p in BACKUP_DIR.glob("data-*.sqlite3"):
        st = p.stat()
        items.append({"name": p.name, "size": st.st_size, "mtime": st.st_mtime})
    items.sort(key=lambda b: b["mtime"], reverse=True)
    return items


def prune_daily_backups():
    daily = sorted(BACKUP_DIR.glob("data-daily-*.sqlite3"))
    for old in daily[:-BACKUP_KEEP] if BACKUP_KEEP > 0 else []:
        old.unlink(missing_ok=True)


_last_backup_day = None


def maybe_daily_backup():
    """Take one backup per day, on the first request of the day.

    Railway has no cron here, so this piggybacks on traffic. A lock file keeps
    the two gunicorn workers from both doing it.
    """
    global _last_backup_day
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if _last_backup_day == day:
        return
    final = BACKUP_DIR / f"data-daily-{day}.sqlite3"
    if final.exists():
        _last_backup_day = day
        return
    lock = BACKUP_DIR / f".daily-{day}.lock"
    try:
        # A lock left by a crashed worker shouldn't block backups forever
        if lock.exists() and time.time() - lock.stat().st_mtime > 600:
            lock.unlink(missing_ok=True)
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return
    try:
        os.close(fd)
        make_backup(f"daily-{day}")
        prune_daily_backups()
        _last_backup_day = day
    except Exception as e:
        print(f"[backup] daily backup failed: {e}", flush=True)
    finally:
        lock.unlink(missing_ok=True)


def visitor_id() -> str:
    # Store a keyed hash, never the raw IP, so tips stay anonymous
    ip = request.remote_addr or "unknown"
    key = str(current_app.secret_key)
    return hmac.new(key.encode(), ip.encode(), hashlib.sha256).hexdigest()[:32]


def rate_limited(kind: str) -> bool:
    """Record a submission and return True if this visitor is over the limit."""
    limit, window = RATE_LIMITS[kind]
    now = time.time()
    who = visitor_id()
    with conn() as c:
        c.execute("DELETE FROM rate_events WHERE ts < ?", (now - 86400,))
        n = c.execute(
            "SELECT COUNT(*) FROM rate_events WHERE visitor = ? AND kind = ? AND ts > ?",
            (who, kind, now - window),
        ).fetchone()[0]
        if n >= limit:
            c.commit()
            return True
        c.execute("INSERT INTO rate_events (visitor, kind, ts) VALUES (?,?,?)", (who, kind, now))
        c.commit()
    return False


def is_bot_submission(data: Dict[str, Any]) -> bool:
    # Hidden "website" field: people never see it, form-filling bots do
    return bool((data.get("website") or "").strip())


def create_app() -> Flask:
    # Serve /public as static files at the site root
    app = Flask(__name__, static_folder="public", static_url_path="")
    # Railway sits in front of the app; trust its one X-Forwarded-For hop for visitor IPs
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    # Allow Cloudflare Pages (different origin) to call /api/*
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    app.secret_key = load_secret_key()
    app.config["UPLOAD_DIR"] = str(UPLOAD_DIR)
    app.config["DB_PATH"] = str(DB_PATH)
    # Per-request cap; the media page uploads several files at once
    app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_MB", "12")) * 1024 * 1024 * 4

    init_db()

    @app.before_request
    def _before():
        maybe_daily_backup()
        maybe_send_digests()
        load_session_user()

    _digest_checked = {"day": None}

    def maybe_send_digests():
        """Once a day after DIGEST_HOUR (Korea time), send everyone's summary.

        Like the daily backup, this rides on traffic (the public site calls the API on
        every page view). digest_runs makes sure only one worker sends each day.
        """
        if not mailer.is_configured():
            return
        now = datetime.now(KST)
        day = now.strftime("%Y-%m-%d")
        if now.hour < DIGEST_HOUR or _digest_checked["day"] == day:
            return
        _digest_checked["day"] = day
        with conn() as c:
            claimed = c.execute("INSERT OR IGNORE INTO digest_runs (day, started_at) VALUES (?, ?)",
                                (day, utc_now_iso())).rowcount
            c.commit()
        if not claimed:
            return
        base = request.url_root.rstrip("/")

        def run():
            with app.app_context():
                try:
                    print(f"[mail] daily summaries: {send_digests(base)}", flush=True)
                except Exception as e:
                    print(f"[mail] daily summaries failed: {e}", flush=True)

        threading.Thread(target=run, daemon=True).start()

    def send_digests(base: str) -> str:
        """Email each person a summary of their queued events. Returns a short report."""
        with conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM notify_queue WHERE sent = 0 ORDER BY id").fetchall()]
            review_now = {r[0] for r in c.execute("SELECT id FROM articles WHERE status = 'review'").fetchall()}
        by_user: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            by_user.setdefault(r["recipient"], []).append(r)
        sent = failed = 0
        today = datetime.now(KST)
        date_label = f"{today:%b} {today.day}"
        for username, events in by_user.items():
            ids = [e["id"] for e in events]
            u = get_user(username)
            # Skip review requests an editor already handled
            events = [e for e in events if e["kind"] != "review" or e["article_id"] in review_now]
            if not u or not u["active"] or not u["email"] or u["notify_mode"] != "daily" or not events:
                mark_sent(ids)
                continue
            groups = digest_groups(events)
            counts = [f"{len(groups['review'])} for review" if groups["review"] else "",
                      f"{sum(len(a['comments']) for a in groups['comments'])} comments" if groups["comments"] else "",
                      f"{len(groups['published'])} published" if groups["published"] else "",
                      f"{len(groups['tips'])} new tips" if groups["tips"] else ""]
            subject = "Newsroom summary: " + ", ".join(x for x in counts if x)
            html = render_template("email_digest.html", name=u["display_name"], g_=groups, date=date_label,
                                   newsroom=base + "/admin", account=base + "/admin/account", tips=base + "/admin/tips")
            text = digest_text(u["display_name"], groups, base)
            try:
                mailer.send(u["email"], subject, html, text, from_addr=mailer.notify_sender())
                mark_sent(ids)
                sent += 1
            except mailer.MailError as e:
                # Stays queued; tomorrow's summary includes it
                print(f"[mail] summary for {username} failed: {e}", flush=True)
                failed += 1
        return f"{sent} sent, {failed} failed"

    def mark_sent(ids):
        with conn() as c:
            c.executemany("UPDATE notify_queue SET sent = 1 WHERE id = ?", [(i,) for i in ids])
            c.execute("DELETE FROM notify_queue WHERE sent = 1 AND created_at < ?",
                      ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
            c.commit()

    def digest_groups(events):
        comments: Dict[str, Dict[str, Any]] = {}
        for e in events:
            if e["kind"] == "comment":
                a = comments.setdefault(e["article_id"], {"title": e["title"], "link": e["link"], "comments": [], "people": []})
                a["comments"].append(e)
                if e["actor"] not in a["people"]:
                    a["people"].append(e["actor"])
        seen = set()
        review = []
        for e in events:
            if e["kind"] == "review" and e["article_id"] not in seen:
                seen.add(e["article_id"])
                review.append(e)
        return {
            "review": review,
            "comments": list(comments.values()),
            "published": [e for e in events if e["kind"] == "published"],
            "tips": [e for e in events if e["kind"] == "tip"],
        }

    def digest_text(name, groups, base):
        out = [f"Hi {name}, here's what happened in the newsroom today.", ""]
        if groups["review"]:
            out.append("WAITING FOR REVIEW")
            out += [f"- {e['title']} (from {e['actor']}): {e['link']}" for e in groups["review"]]
            out.append("")
        if groups["comments"]:
            out.append("COMMENTS")
            for a in groups["comments"]:
                out.append(f"- {len(a['comments'])} on “{a['title']}” from {', '.join(a['people'])}: {a['link']}")
                out.append(f"  Latest: {a['comments'][-1]['detail'][:200]}")
            out.append("")
        if groups["published"]:
            out.append("PUBLISHED")
            out += [f"- {e['title']} (by {e['actor']}): {e['link']}" for e in groups["published"]]
            out.append("")
        if groups["tips"]:
            out += [f"NEW TIPS: {len(groups['tips'])} — {base}/admin/tips", ""]
        out.append(f"Change how often you get these: {base}/admin/account")
        out.append(NO_REPLY_TEXT)
        return "\n".join(out)

    @app.post("/admin/digests/send")
    @require_admin
    def admin_digests_send():
        if not verify_csrf():
            abort(400)
        if not mailer.is_configured():
            flash("Email isn't set up yet (RESEND_API_KEY is missing).", "error")
        else:
            flash(f"Daily summaries: {send_digests(request.url_root.rstrip('/'))}.", "ok")
        return redirect(url_for("admin_users"))

    @app.template_filter("timestamp_utc")
    def _timestamp_utc(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    @app.context_processor
    def _template_globals():
        return {
            "public_site_url": PUBLIC_SITE_URL,
            "contact_email": CONTACT_EMAIL,
            "user": g.get("user"),
            "status_labels": STATUS_LABELS,
            "role_labels": ROLE_LABELS,
            "nav": nav_counts(),
        }

    def nav_counts() -> Dict[str, int]:
        """Badges in the admin nav: what's waiting for this person."""
        user = g.get("user")
        if not user:
            return {}
        with conn() as c:
            if user["role"] == "writer":
                feedback = c.execute(
                    """SELECT COUNT(*) FROM comments JOIN articles ON articles.id = comments.article_id
                       WHERE comments.resolved = 0 AND articles.created_by = ?""", (user["username"],)).fetchone()[0]
                return {"feedback": feedback}
            return {
                "review": c.execute("SELECT COUNT(*) FROM articles WHERE status = 'review'").fetchone()[0],
                "tips": c.execute("SELECT COUNT(*) FROM tips WHERE status = 'new'").fetchone()[0],
            }

    @app.get("/")
    def home():
        # The backend's root isn't a page; send visitors to the public site
        return redirect(PUBLIC_SITE_URL)

    @app.get("/favicon.ico")
    def favicon():
        logo = PUBLIC_DIR / "assets" / "logo.png"
        if logo.exists():
            return send_from_directory(logo.parent, logo.name)
        abort(404)

    @app.get("/assets/uploads/<path:filename>")
    def uploaded_assets(filename: str):
        """Serve uploaded media from the persistent upload directory.

        Articles store cover images as /assets/uploads/<filename>.
        These files live in UPLOAD_DIR (typically a Railway Volume).
        """
        return send_from_directory(UPLOAD_DIR, filename)

    # ---------- Public API ----------
    @app.get("/api/content")
    def api_content():
        with conn() as c:
            rows = c.execute("SELECT * FROM articles WHERE status = 'published' ORDER BY date DESC").fetchall()
        articles = [public_article(row_to_article(r)) for r in rows]
        return jsonify({"sections": SECTIONS, "articles": articles, "settings": get_settings()})

    @app.get("/api/article/<article_id>")
    def api_article(article_id: str):
        # Used by the Cloudflare Pages function that adds link-preview tags
        with conn() as c:
            r = c.execute("SELECT * FROM articles WHERE id = ? AND status = 'published'", (article_id,)).fetchone()
        if not r:
            return jsonify({"ok": False, "error": "Not found"}), 404
        a = row_to_article(r)
        return jsonify({k: a[k] for k in ("id", "title", "section", "author", "date", "summary", "cover")})

    @app.post("/api/subscribe")
    def api_subscribe():
        data = request.get_json(silent=True) or {}
        if is_bot_submission(data):
            return jsonify({"ok": True})
        if rate_limited("subscribe"):
            return jsonify({"ok": False, "error": "Too many attempts. Try again later."}), 429
        email = (data.get("email") or "").strip()
        if not add_subscriber(email):
            return jsonify({"ok": False, "error": "Please enter a valid email."}), 400
        return jsonify({"ok": True})

    @app.post("/api/tip")
    def api_tip():
        data = request.get_json(silent=True) or {}
        if is_bot_submission(data):
            return jsonify({"ok": True})
        msg = (data.get("message") or "").strip()
        if len(msg) > MAX_TIP_CHARS:
            return jsonify({"ok": False, "error": f"Tip is too long (max {MAX_TIP_CHARS} characters)."}), 400
        if rate_limited("tip"):
            return jsonify({"ok": False, "error": "Too many tips from you in the last hour. Try again later."}), 429
        if not add_tip(msg):
            return jsonify({"ok": False, "error": "Tip is too short."}), 400
        notify(staff_recipients(roles=("editor", "admin")), "tips", "New anonymous tip",
               "A new tip arrived in the newsroom inbox.", [msg[:500]], admin_url("/admin/tips"), "Open tips",
               event={"kind": "tip", "detail": msg})
        return jsonify({"ok": True})

    # ---------- Admin auth ----------
    @app.get("/admin/login")
    def admin_login():
        return render_template("admin_login.html", next=request.args.get("next", ""), csrf=csrf_token())

    @app.post("/admin/login")
    def admin_login_post():
        if not verify_csrf():
            abort(400)
        if rate_limited("login"):
            flash("Too many login attempts. Wait 15 minutes and try again.", "error")
            return redirect(url_for("admin_login"))
        username = (request.form.get("username") or "").strip().lower()
        pwd = request.form.get("password", "")

        fingerprint = None
        if username == BUILTIN_ADMIN:
            expected = admin_password()
            if not expected:
                flash("The admin login is disabled until HAFS_ADMIN_PASSWORD is set on the server.", "error")
                return redirect(url_for("admin_login"))
            if secrets.compare_digest(pwd.encode(), expected.encode()):
                fingerprint = password_fingerprint(expected)
        else:
            r = get_user(username)
            if r and r["active"] and check_password_hash(r["password_hash"], pwd):
                fingerprint = password_fingerprint(r["password_hash"])

        if not fingerprint:
            flash("Wrong username or password.", "error")
            return redirect(url_for("admin_login", next=request.form.get("next") or ""))
        session.clear()
        session["user"] = username
        session["pwv"] = fingerprint
        flash("Logged in.", "ok")
        return redirect(safe_next(request.form.get("next") or "/admin"))

    @app.get("/admin/logout")
    def admin_logout():
        session.clear()
        flash("Logged out.", "ok")
        return redirect(url_for("admin_login"))

    @app.errorhandler(403)
    def _forbidden(_e):
        return render_template("admin_message.html", title="Not allowed",
                               message="Your account doesn't have permission to do that. Ask an editor."), 403

    # ---------- Admin: articles ----------
    def load_article(article_id: str) -> Dict[str, Any]:
        with conn() as c:
            r = c.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if not r:
            abort(404)
        return row_to_article(r)

    def load_comments(article_id: str):
        with conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM comments WHERE article_id = ? ORDER BY created_at, id", (article_id,)).fetchall()]

    def can_view_article(a: Dict[str, Any]) -> bool:
        return g.user["role"] != "writer" or a.get("created_by") == g.user["username"]

    def render_editor(a: Dict[str, Any], is_new: bool):
        feedback = [] if is_new else [cm for cm in load_comments(a["id"]) if not cm["resolved"]]
        return render_template("admin_edit.html", a=a, sections=SECTIONS, csrf=csrf_token(), is_new=is_new,
                               can_publish=g.user["role"] in ("editor", "admin"), feedback=feedback)

    @app.get("/admin")
    @require_login
    def admin_index():
        with conn() as c:
            if g.user["role"] == "writer":
                rows = c.execute("SELECT * FROM articles WHERE created_by = ? ORDER BY date DESC", (g.user["username"],)).fetchall()
            else:
                # Articles waiting for review first, so editors see them
                rows = c.execute("SELECT * FROM articles ORDER BY status = 'review' DESC, date DESC").fetchall()
        articles = [row_to_article(r) for r in rows]
        with conn() as c:
            open_counts = dict(c.execute(
                "SELECT article_id, COUNT(*) FROM comments WHERE resolved = 0 GROUP BY article_id").fetchall())
        for a in articles:
            a["editable"] = can_edit_article(g.user, a)
            a["open_comments"] = open_counts.get(a["id"], 0)
        counts = {k: sum(1 for a in articles if a["status"] == k) for k in STATUSES}
        counts["all"] = len(articles)
        # Editors land on the review queue when something is waiting
        default = "review" if g.user["role"] != "writer" and counts["review"] else "all"
        show = request.args.get("show", default)
        if show in STATUSES:
            articles = [a for a in articles if a["status"] == show]
        else:
            show = "all"
        return render_template("admin_list.html", articles=articles, counts=counts, show=show, csrf=csrf_token(),
                               title="Articles")

    @app.get("/admin/new")
    @require_login
    def admin_new():
        s = get_settings()
        a = {
            "id": "",
            "title": "",
            "section": "News",
            "author": g.user["name"] if not g.user["builtin"] else STAFF_BYLINE,
            "date": datetime.now().strftime("%Y-%m-%d"),
            "summary": "",
            "cover": "",
            "issue": int(s.get("currentIssueNumber", 0) or 0),
            "pullQuote": "",
            "body": [],
            "status": "draft",
            "correction": "",
        }
        return render_editor(a, is_new=True)

    @app.get("/admin/edit/<article_id>")
    @require_login
    def admin_edit(article_id: str):
        a = load_article(article_id)
        if not can_edit_article(g.user, a):
            abort(403)
        return render_editor(a, is_new=False)

    @app.get("/admin/preview/<article_id>")
    @require_login
    def admin_preview(article_id: str):
        a = load_article(article_id)
        if not can_view_article(a):
            abort(403)
        show_resolved = request.args.get("resolved") == "1"
        comments = [cm for cm in load_comments(article_id) if show_resolved or not cm["resolved"]]
        by_block, general, orphaned = anchor_comments(a["body"], comments)
        blocks = [{"i": i, "html": render_body_block(p), "comments": by_block[i]} for i, p in enumerate(a["body"])]
        resolved_count = sum(1 for cm in load_comments(article_id) if cm["resolved"])
        return render_template("admin_preview.html", a=a, blocks=blocks, general=general, orphaned=orphaned,
                               show_resolved=show_resolved, resolved_count=resolved_count, csrf=csrf_token(),
                               title=f"Preview: {a['title']}", editable=can_edit_article(g.user, a))

    @app.get("/admin/history/<article_id>")
    @require_login
    def admin_history(article_id: str):
        a = load_article(article_id)
        if not can_view_article(a):
            abort(403)
        with conn() as c:
            revs = [dict(r) for r in c.execute(
                "SELECT * FROM revisions WHERE article_id = ? ORDER BY id DESC", (article_id,)).fetchall()]
        for r in revs:
            r["snapshot"] = json.loads(r["snapshot_json"])

        current = {k: a.get(k) for k in REVISION_FIELDS}
        selected = None
        try:
            rev_id = int(request.args.get("rev", "0"))
        except ValueError:
            rev_id = 0
        if revs:
            selected = next((r for r in revs if r["id"] == rev_id), revs[0])
        fields, paras, against_label = [], [], ""
        if selected:
            if request.args.get("vs") == "current":
                # What changed from this version to now
                fields, paras = diff_articles(selected["snapshot"], current)
                against_label = "from this version to the current text"
            else:
                idx = revs.index(selected)
                before = revs[idx + 1]["snapshot"] if idx + 1 < len(revs) else {k: "" for k in REVISION_FIELDS} | {"body": []}
                fields, paras = diff_articles(before, selected["snapshot"])
                against_label = "in this save" if idx + 1 < len(revs) else "in the first saved version"
        return render_template("admin_history.html", a=a, revs=revs, selected=selected, fields=fields, paras=paras,
                               against_label=against_label, vs_current=request.args.get("vs") == "current",
                               editable=can_edit_article(g.user, a), csrf=csrf_token(), title=f"History: {a['title']}")

    @app.post("/admin/history/<article_id>/restore/<int:rev_id>")
    @require_login
    def admin_history_restore(article_id: str, rev_id: int):
        if not verify_csrf():
            abort(400)
        a = load_article(article_id)
        if not can_edit_article(g.user, a):
            abort(403)
        with conn() as c:
            r = c.execute("SELECT * FROM revisions WHERE id = ? AND article_id = ?", (rev_id, article_id)).fetchone()
        if not r:
            abort(404)
        # Bring back the text; status, lead and authorship stay as they are now
        a.update(json.loads(r["snapshot_json"]))
        a["updated_by"] = g.user["name"]
        upsert_article(a)
        saved = r["saved_at"][:16].replace("T", " ")
        record_revision(a, g.user["name"], note=f"Restored version from {saved} UTC")
        flash(f"Restored the version from {saved} UTC. The previous text is still in History.", "ok")
        return redirect(url_for("admin_edit", article_id=article_id))

    @app.post("/admin/comments/<article_id>")
    @require_login
    def admin_comment_add(article_id: str):
        if not verify_csrf():
            abort(400)
        a = load_article(article_id)
        if not can_view_article(a):
            abort(403)
        body = (request.form.get("body") or "").strip()
        try:
            block = int(request.form.get("block", "-1"))
        except ValueError:
            block = -1
        if block >= len(a["body"]):
            block = -1
        if not body or len(body) > MAX_COMMENT_CHARS:
            flash(f"Comments must be 1–{MAX_COMMENT_CHARS} characters.", "error")
        else:
            with conn() as c:
                c.execute(
                    "INSERT INTO comments (article_id, block, quote, body, author, author_name, created_at) VALUES (?,?,?,?,?,?,?)",
                    (article_id, block, a["body"][block] if block >= 0 else "", body,
                     g.user["username"], g.user["name"], utc_now_iso()),
                )
                c.commit()
                # Everyone in the conversation: the writer plus earlier commenters
                involved = {a["created_by"]} | {r[0] for r in c.execute(
                    "SELECT DISTINCT author FROM comments WHERE article_id = ?", (article_id,)).fetchall()}
            notify(staff_recipients(usernames=tuple(involved), exclude=(g.user["username"],)),
                   f"comments:{article_id}", f"New feedback on “{a['title']}”",
                   f"{g.user['name']} commented on “{a['title']}”.", [body[:500]],
                   admin_url(f"/admin/preview/{article_id}"), "See the comments",
                   event={"kind": "comment", "article_id": article_id, "title": a["title"],
                          "actor": g.user["name"], "detail": body})
        anchor = f"#block-{block}" if block >= 0 else "#general"
        return redirect(url_for("admin_preview", article_id=article_id) + anchor)

    @app.post("/admin/comments/<int:cid>/<action>")
    @require_login
    def admin_comment_update(cid: int, action: str):
        if not verify_csrf():
            abort(400)
        with conn() as c:
            cm = c.execute("SELECT * FROM comments WHERE id = ?", (cid,)).fetchone()
        if not cm:
            abort(404)
        a = load_article(cm["article_id"])
        if not can_view_article(a):
            abort(403)
        is_editor = g.user["role"] in ("editor", "admin")
        with conn() as c:
            if action in ("resolve", "reopen"):
                # The commenter, the article's writer, or any editor
                if not (is_editor or cm["author"] == g.user["username"] or a["created_by"] == g.user["username"]):
                    abort(403)
                c.execute("UPDATE comments SET resolved = ? WHERE id = ?", (1 if action == "resolve" else 0, cid))
            elif action == "delete":
                if not (is_editor or cm["author"] == g.user["username"]):
                    abort(403)
                c.execute("DELETE FROM comments WHERE id = ?", (cid,))
            else:
                abort(400)
            c.commit()
        back = request.form.get("back") or ""
        if back.startswith("/admin/"):
            return redirect(back)
        return redirect(url_for("admin_preview", article_id=cm["article_id"]))

    @app.post("/admin/save")
    @require_login
    def admin_save():
        if not verify_csrf():
            abort(400)

        is_new = request.form.get("is_new") == "1"
        article_id = (request.form.get("id") or "").strip()

        try:
            issue = int((request.form.get("issue") or "0").strip() or "0")
        except ValueError:
            issue = 0

        body_raw = (request.form.get("body") or "").replace("\r\n", "\n")
        body = [p.strip() for p in re.split(r"\n\s*\n", body_raw) if p.strip()]

        status = request.form.get("status") if request.form.get("status") in STATUSES else "draft"
        if status == "published" and g.user["role"] == "writer":
            status = "review"

        a = {
            "id": article_id,
            "title": (request.form.get("title") or "").strip(),
            "section": (request.form.get("section") or "News").strip(),
            "author": clean_author(request.form.get("author") or ""),
            "date": (request.form.get("date") or "").strip(),
            "summary": (request.form.get("summary") or "").strip(),
            "cover": (request.form.get("cover") or "").strip(),
            "issue": issue,
            "pullQuote": (request.form.get("pullQuote") or "").strip(),
            "body": body,
            "status": status,
            "correction": (request.form.get("correction") or "").strip(),
            "updated_by": g.user["name"],
        }
        if a["section"] not in SECTIONS:
            a["section"] = "News"

        def fail(msg: str):
            # Re-show the form with what was typed instead of losing it
            flash(msg, "error")
            return render_editor(a, is_new=is_new)

        id_note = ""
        if is_new:
            # The link is made from whatever was typed in the ID box, else the title;
            # a taken link gets -2, -3 … rather than overwriting another article
            base = make_slug(article_id) or make_slug(a["title"]) or f"article-{datetime.now(KST):%Y%m%d}"
            article_id, n = base, 1
            with conn() as c:
                while c.execute("SELECT 1 FROM articles WHERE id = ?", (article_id,)).fetchone():
                    n += 1
                    article_id = f"{base[:MAX_SLUG_LEN - 4]}-{n}"
            if n > 1:
                id_note = f" Its link is article.html?id={article_id} because “{base}” was taken."
            a["id"] = article_id

        with conn() as c:
            existing = c.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if not is_new:
            if not existing:
                abort(404)
            if not can_edit_article(g.user, row_to_article(existing)):
                abort(403)
        a["created_by"] = existing["created_by"] if existing else g.user["username"]
        if not a["author"]:
            # Blank byline: credit whoever created the article
            creator = get_user(a["created_by"])
            a["author"] = creator["display_name"] if creator else (g.user["name"] if not g.user["builtin"] else STAFF_BYLINE)

        f = request.files.get("cover_upload")
        if f and getattr(f, "filename", ""):
            try:
                a["cover"] = f"/assets/uploads/{save_upload_image(f, UPLOAD_DIR)}"
            except ValueError:
                return fail("Cover must be a PNG, JPG, WEBP or GIF image.")

        # Only editors pick the lead story; keep the existing choice when a writer saves
        if g.user["role"] in ("editor", "admin"):
            a["lead"] = request.form.get("lead") == "1"
        else:
            a["lead"] = bool(existing["featured"]) if existing else False

        if existing:
            with conn() as c:
                has_history = c.execute("SELECT 1 FROM revisions WHERE article_id = ? LIMIT 1", (article_id,)).fetchone()
            if not has_history:
                # Articles from before history existed: keep their old text as the first version
                before = row_to_article(existing)
                record_revision(before, before["updated_by"] or "Earlier version", note="Text before history was recorded")

        prev_status = existing["status"] if existing else None
        upsert_article(a)
        if status == "review" and prev_status != "review":
            notify(staff_recipients(roles=("editor", "admin"), exclude=(g.user["username"],)),
                   f"review:{article_id}", f"For review: {a['title']}",
                   f"{g.user['name']} submitted an article for review.",
                   [f"“{a['title']}” ({a['section']})"], admin_url(f"/admin/preview/{article_id}"), "Review it",
                   event={"kind": "review", "article_id": article_id, "title": a["title"], "actor": g.user["name"]})
        if status == "published" and prev_status != "published" and a["created_by"] != g.user["username"]:
            notify(staff_recipients(usernames=(a["created_by"],)), f"published:{article_id}",
                   f"Published: {a['title']}", "Your article is live on the site.",
                   [f"“{a['title']}” was published by {g.user['name']}."],
                   f"{PUBLIC_SITE_URL}/article.html?id={article_id}", "Read it on the site",
                   event={"kind": "published", "article_id": article_id, "title": a["title"], "actor": g.user["name"]})
        if a["lead"]:
            # One lead per issue
            with conn() as c:
                c.execute("UPDATE articles SET featured = 0 WHERE issue = ? AND id != ?", (a["issue"], article_id))
                c.commit()
        record_revision(a, g.user["name"])
        flash({
            "published": "Published — it's live on the site.",
            "review": "Submitted for review. An editor will publish it.",
            "draft": "Saved as draft (not visible on the site).",
        }[status] + id_note, "ok")
        if not can_edit_article(g.user, a):
            return redirect(url_for("admin_index"))
        return redirect(url_for("admin_edit", article_id=article_id))

    @app.post("/admin/delete/<article_id>")
    @require_login
    def admin_delete(article_id: str):
        if not verify_csrf():
            abort(400)
        if not can_edit_article(g.user, load_article(article_id)):
            abort(403)
        delete_article(article_id)
        flash("Deleted.", "ok")
        return redirect(url_for("admin_index"))

    # ---------- Admin: users ----------
    def user_form_error(username: str, name: str, role: str, password: Optional[str]) -> Optional[str]:
        if username is not None and not re.match(r"^[a-z0-9][a-z0-9._-]{1,31}$", username):
            return "Username must be 2–32 lowercase letters, numbers, dots, dashes or underscores."
        if username == BUILTIN_ADMIN:
            return "“admin” is reserved for the built-in admin login."
        if name is not None and not name:
            return "Display name is required."
        if role is not None and role not in ROLES:
            return "Pick a role."
        if password is not None and len(password) < MIN_PASSWORD_LEN:
            return f"Password must be at least {MIN_PASSWORD_LEN} characters."
        return None

    @app.get("/admin/users")
    @require_admin
    def admin_users():
        return render_template("admin_users.html", users=list_users(), roles=ROLES, csrf=csrf_token(),
                               min_len=MIN_PASSWORD_LEN, title="Users")

    @app.post("/admin/users/create")
    @require_admin
    def admin_users_create():
        if not verify_csrf():
            abort(400)
        username = (request.form.get("username") or "").strip().lower()
        name = (request.form.get("display_name") or "").strip()
        role = request.form.get("role") or ""
        password = request.form.get("password") or ""
        email = (request.form.get("email") or "").strip().lower()
        err = user_form_error(username, name, role, password)
        if not err and email and not is_valid_email(email):
            err = "That email address doesn't look right."
        if not err and get_user(username):
            err = f"The username “{username}” is taken."
        if err:
            flash(err, "error")
            return redirect(url_for("admin_users"))
        with conn() as c:
            c.execute(
                "INSERT INTO users (username, display_name, role, password_hash, active, created_at, email) VALUES (?,?,?,?,1,?,?)",
                (username, name, role, generate_password_hash(password), utc_now_iso(), email),
            )
            c.commit()
        flash(f"Created {name} ({ROLE_LABELS[role]}). Give them their username and password.", "ok")
        return redirect(url_for("admin_users"))

    @app.post("/admin/users/update/<username>")
    @require_admin
    def admin_users_update(username: str):
        if not verify_csrf():
            abort(400)
        r = get_user(username)
        if not r:
            abort(404)
        action = request.form.get("action")
        with conn() as c:
            if action == "role":
                role = request.form.get("role") or ""
                if role not in ROLES:
                    abort(400)
                c.execute("UPDATE users SET role = ? WHERE username = ?", (role, username))
                flash(f"{r['display_name']} is now {ROLE_LABELS[role]}.", "ok")
            elif action == "password":
                password = request.form.get("password") or ""
                err = user_form_error(None, None, None, password)
                if err:
                    flash(err, "error")
                    return redirect(url_for("admin_users"))
                c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (generate_password_hash(password), username))
                flash(f"Password reset for {r['display_name']}. They've been signed out everywhere.", "ok")
            elif action in ("deactivate", "activate"):
                c.execute("UPDATE users SET active = ? WHERE username = ?", (1 if action == "activate" else 0, username))
                flash(f"{r['display_name']} {'can sign in again' if action == 'activate' else 'can no longer sign in'}.", "ok")
            elif action == "delete":
                # Their articles stay; created_by keeps the old username
                c.execute("DELETE FROM users WHERE username = ?", (username,))
                flash(f"Deleted {r['display_name']}'s account. Their articles are kept.", "ok")
            else:
                abort(400)
            c.commit()
        return redirect(url_for("admin_users"))

    @app.get("/admin/account")
    @require_login
    def admin_account():
        me = None if g.user["builtin"] else dict(get_user(g.user["username"]))
        return render_template("admin_account.html", csrf=csrf_token(), min_len=MIN_PASSWORD_LEN, title="Account",
                               me=me, mail_ready=mailer.is_configured(), modes=NOTIFY_MODES, digest_hour=DIGEST_HOUR)

    @app.post("/admin/account/email")
    @require_login
    def admin_account_email():
        if not verify_csrf():
            abort(400)
        if g.user["builtin"]:
            abort(400)
        email = (request.form.get("email") or "").strip().lower()
        if email and not is_valid_email(email):
            flash("That email address doesn't look right.", "error")
            return redirect(url_for("admin_account"))
        with conn() as c:
            mode = request.form.get("notify_mode")
            c.execute("UPDATE users SET email = ?, notify_mode = ? WHERE username = ?",
                      (email, mode if mode in NOTIFY_MODES else "daily", g.user["username"]))
            c.commit()
        flash("Email settings saved.", "ok")
        return redirect(url_for("admin_account"))

    @app.post("/admin/account")
    @require_login
    def admin_account_post():
        if not verify_csrf():
            abort(400)
        if g.user["builtin"]:
            flash("The built-in admin password is set with HAFS_ADMIN_PASSWORD on Railway.", "error")
            return redirect(url_for("admin_account"))
        r = get_user(g.user["username"])
        if not check_password_hash(r["password_hash"], request.form.get("current") or ""):
            flash("Current password is wrong.", "error")
            return redirect(url_for("admin_account"))
        new = request.form.get("new") or ""
        err = user_form_error(None, None, None, new)
        if not err and new != request.form.get("confirm"):
            err = "The new passwords don't match."
        if err:
            flash(err, "error")
            return redirect(url_for("admin_account"))
        new_hash = generate_password_hash(new)
        with conn() as c:
            c.execute("UPDATE users SET password_hash = ? WHERE username = ?", (new_hash, r["username"]))
            c.commit()
        session["pwv"] = password_fingerprint(new_hash)  # stay signed in here, sign out elsewhere
        flash("Password changed.", "ok")
        return redirect(url_for("admin_account"))

    # ---------- Admin: subscribers & tips ----------
    @app.get("/admin/subscribers")
    @require_admin
    def admin_subscribers():
        with conn() as c:
            rows = c.execute("SELECT * FROM subscribers ORDER BY created_at DESC").fetchall()
        subs = [dict(r) for r in rows]
        return render_template("admin_subscribers.html", subscribers=subs, csrf=csrf_token())

    @app.post("/admin/subscribers/delete/<int:sid>")
    @require_admin
    def admin_subscribers_delete(sid: int):
        if not verify_csrf():
            abort(400)
        with conn() as c:
            c.execute("DELETE FROM subscribers WHERE id = ?", (sid,))
            c.commit()
        flash("Subscriber removed.", "ok")
        return redirect(url_for("admin_subscribers"))

    @app.get("/admin/tips")
    @require_editor
    def admin_tips():
        with conn() as c:
            rows = c.execute("SELECT * FROM tips ORDER BY created_at DESC").fetchall()
        tips = [dict(r) for r in rows]
        counts = {k: sum(1 for t in tips if t["status"] == k) for k in ("new", "read", "resolved")}
        counts["all"] = len(tips)
        show = request.args.get("show", "new" if counts["new"] else "all")
        if show != "all":
            tips = [t for t in tips if t["status"] == show]
        return render_template("admin_tips.html", tips=tips, counts=counts, show=show, csrf=csrf_token(), title="Tips")

    @app.post("/admin/tips/mark/<int:tid>")
    @require_editor
    def admin_tips_mark(tid: int):
        if not verify_csrf():
            abort(400)
        status = request.form.get("status", "read")
        if status not in ("new", "read", "resolved"):
            status = "read"
        with conn() as c:
            c.execute("UPDATE tips SET status = ? WHERE id = ?", (status, tid))
            c.commit()
        flash("Tip updated.", "ok")
        return redirect(url_for("admin_tips", show=request.form.get("show") or None))

    @app.post("/admin/tips/delete/<int:tid>")
    @require_editor
    def admin_tips_delete(tid: int):
        if not verify_csrf():
            abort(400)
        with conn() as c:
            c.execute("DELETE FROM tips WHERE id = ?", (tid,))
            c.commit()
        flash("Tip deleted.", "ok")
        return redirect(url_for("admin_tips"))

    # ---------- Admin: settings/export ----------
    @app.get("/admin/settings")
    @require_editor
    def admin_settings():
        settings = get_settings()
        staff_lines = []
        for s in settings.get("staff", []):
            role = (s.get("role") or "").strip()
            name = (s.get("name") or "").strip()
            contact = (s.get("contact") or "").strip()
            staff_lines.append(f"{role} — {name}" + (f" — {contact}" if contact else ""))
        return render_template(
            "admin_settings.html",
            settings=settings,
            staff_text="\n".join(staff_lines),
            csrf=csrf_token(),
        )

    @app.post("/admin/settings")
    @require_editor
    def admin_settings_post():
        if not verify_csrf():
            abort(400)

        settings = get_settings()

        try:
            settings["currentIssueNumber"] = int((request.form.get("currentIssueNumber") or "0").strip() or "0")
        except ValueError:
            settings["currentIssueNumber"] = 0

        settings["currentIssueDate"] = (request.form.get("currentIssueDate") or "").strip()

        settings.pop("wittyQuotes", None)  # retired setting

        staff_text = (request.form.get("staff") or "").replace("\r\n", "\n")
        staff = []
        for line in [l.strip() for l in staff_text.split("\n") if l.strip()]:
            # "Role — Name — Contact"; plain " - " or "|" also work (easier to type on a phone)
            parts = [p.strip() for p in re.split(r"\s+[—–-]\s+|\s*[—|]\s*", line)]
            if len(parts) < 2:
                continue
            role = parts[0]
            name = parts[1] if len(parts) >= 2 else ""
            contact = parts[2] if len(parts) >= 3 else ""
            staff.append({"role": role, "name": name, "contact": contact})
        settings["staff"] = staff

        set_settings(settings)
        flash("Settings saved.", "ok")
        return redirect(url_for("admin_settings"))

    # ---------- Admin: backups ----------
    @app.get("/admin/backups")
    @require_admin
    def admin_backups():
        return render_template(
            "admin_backups.html",
            backups=list_backups(),
            keep=BACKUP_KEEP,
            backup_dir=str(BACKUP_DIR),
            csrf=csrf_token(),
            title="Backups",
        )

    @app.post("/admin/backups/create")
    @require_admin
    def admin_backups_create():
        if not verify_csrf():
            abort(400)
        out = make_backup("manual-" + datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S"))
        flash(f"Backup created: {out.name}", "ok")
        return redirect(url_for("admin_backups"))

    @app.get("/admin/backups/download/<name>")
    @require_admin
    def admin_backups_download(name: str):
        p = backup_path(name)
        if not p:
            abort(404)
        return send_file(p, as_attachment=True, download_name=p.name)

    @app.post("/admin/backups/restore")
    @require_admin
    def admin_backups_restore():
        if not verify_csrf():
            abort(400)
        p = backup_path(request.form.get("name", ""))
        if not p:
            abort(400)
        # Snapshot the current state first so a restore can itself be undone
        safety = make_backup("before-restore-" + datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S"))
        src = sqlite3.connect(p)
        try:
            with conn() as dst:
                src.backup(dst)
        finally:
            src.close()
        init_db()  # bring an older backup up to the current schema
        flash(f"Restored {p.name}. The previous state was saved as {safety.name}.", "ok")
        return redirect(url_for("admin_backups"))

    @app.post("/admin/backups/delete")
    @require_admin
    def admin_backups_delete():
        if not verify_csrf():
            abort(400)
        p = backup_path(request.form.get("name", ""))
        if not p:
            abort(400)
        p.unlink()
        flash(f"Deleted {p.name}.", "ok")
        return redirect(url_for("admin_backups"))

    @app.get("/admin/backups/full.zip")
    @require_admin
    def admin_backups_full():
        """Database snapshot plus every uploaded image, for keeping a copy off Railway."""
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tmp_dir = Path(tempfile.mkdtemp())
        db_copy = tmp_dir / "data.sqlite3"
        with conn() as src:
            dst = sqlite3.connect(db_copy)
            try:
                src.backup(dst)
            finally:
                dst.close()
        zip_path = tmp_dir / f"hafs-herald-backup-{stamp}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(db_copy, "data.sqlite3")
            for f in UPLOAD_DIR.rglob("*"):
                if f.is_file():
                    z.write(f, "uploads/" + f.relative_to(UPLOAD_DIR).as_posix())
        db_copy.unlink()
        # Unlink now; the open handle keeps the data readable until the response finishes
        fh = open(zip_path, "rb")
        zip_path.unlink()
        tmp_dir.rmdir()
        return send_file(fh, as_attachment=True, download_name=zip_path.name, mimetype="application/zip")

    # ---------- Newsletter ----------
    def issue_articles(issue: int) -> List[Dict[str, Any]]:
        with conn() as c:
            rows = c.execute("SELECT * FROM articles WHERE status = 'published' AND issue = ? ORDER BY featured DESC, date DESC",
                             (issue,)).fetchall()
        return [row_to_article(r) for r in rows]

    def absolute_media(path: str) -> str:
        if not path or re.match(r"^https?://", path):
            return path
        return request.url_root.rstrip("/") + "/" + path.lstrip("/")

    def newsletter_content(issue: int, subject: str, intro: str, unsubscribe_url: str):
        """HTML and plain-text versions of an issue's newsletter."""
        arts = issue_articles(issue)
        lead = next((a for a in arts if a["lead"]), None) or next((a for a in arts if a["cover"]), None)
        rest = [a for a in arts if a is not lead]
        settings = get_settings()
        date = settings.get("currentIssueDate") if int(settings.get("currentIssueNumber") or 0) == issue else ""
        link = lambda a: f"{PUBLIC_SITE_URL}/article.html?id={a['id']}"
        html = render_template("email_newsletter.html", issue=issue, date=date, intro=intro, lead=lead, rest=rest,
                               link=link, cover=lambda a: absolute_media(a["cover"]), site=PUBLIC_SITE_URL,
                               unsubscribe_url=unsubscribe_url, subject=subject)
        lines = [f"THE HAFS HERALD — Issue #{issue}" + (f" · {date}" if date else ""), ""]
        if intro:
            lines += [intro, ""]
        for a in ([lead] if lead else []) + rest:
            lines += [f"[{a['section']}] {a['title']}", a["summary"], f"By {a['author']} — {link(a)}", ""]
        lines += [f"Read the whole issue: {PUBLIC_SITE_URL}/issue.html?n={issue}", "",
                  f"Unsubscribe: {unsubscribe_url}", "", NO_REPLY_TEXT]
        return html, "\n".join(lines), len(arts)

    def unsubscribe_link(token: str) -> str:
        return request.url_root.rstrip("/") + "/unsubscribe?t=" + token

    def deliver(send_id: int) -> str:
        """Send a newsletter to everyone who hasn't had it yet. Returns a message for the admin."""
        with conn() as c:
            send = c.execute("SELECT * FROM newsletter_sends WHERE id = ?", (send_id,)).fetchone()
            todo = c.execute(
                """SELECT * FROM subscribers WHERE created_at <= ? AND id NOT IN
                   (SELECT subscriber_id FROM newsletter_recipients WHERE send_id = ?) ORDER BY id""",
                (send["started_at"], send_id)).fetchall()
        sent = 0
        error = ""
        for i in range(0, len(todo), mailer.BATCH_SIZE):
            batch = todo[i:i + mailer.BATCH_SIZE]
            msgs = []
            for sub in batch:
                url = unsubscribe_link(sub["token"])
                html, text, _ = newsletter_content(send["issue"], send["subject"], send["intro"], url)
                msgs.append(mailer.message(sub["email"], send["subject"], html, text, headers={
                    "List-Unsubscribe": f"<{url}>",
                    "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
                }))
            try:
                mailer.send_batch(msgs)
            except mailer.MailError as e:
                error = ("The email service's sending limit was reached (Resend's free plan allows 100 a day). "
                         "Click “Continue sending” tomorrow to send the rest." if e.is_rate_limit else str(e))
                break
            with conn() as c:
                c.executemany("INSERT OR IGNORE INTO newsletter_recipients (send_id, subscriber_id) VALUES (?,?)",
                              [(send_id, sub["id"]) for sub in batch])
                c.commit()
            sent += len(batch)
        remaining = len(todo) - sent
        with conn() as c:
            c.execute("UPDATE newsletter_sends SET finished = ?, last_error = ? WHERE id = ?",
                      (1 if remaining == 0 else 0, error, send_id))
            c.commit()
        if remaining == 0:
            return f"Sent to {sent} subscriber{'s' if sent != 1 else ''}. Done."
        return f"Sent to {sent}; {remaining} still to go. {error}"

    @app.get("/admin/newsletter")
    @require_admin
    def admin_newsletter():
        settings = get_settings()
        with conn() as c:
            issues = [r[0] for r in c.execute(
                "SELECT DISTINCT issue FROM articles WHERE status = 'published' AND issue > 0 ORDER BY issue DESC").fetchall()]
            sends = [dict(r) for r in c.execute(
                """SELECT s.*, (SELECT COUNT(*) FROM newsletter_recipients r WHERE r.send_id = s.id) AS sent
                   FROM newsletter_sends s ORDER BY s.id DESC LIMIT 20""").fetchall()]
            subscriber_count = c.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
        try:
            issue = int(request.args.get("issue") or settings.get("currentIssueNumber") or (issues[0] if issues else 0))
        except ValueError:
            issue = 0
        subject = request.args.get("subject") or f"The HAFS Herald — Issue #{issue}"
        intro = request.args.get("intro") or ""
        preview_html, _, article_count = newsletter_content(issue, subject, intro, unsubscribe_link("preview"))
        me = None if g.user["builtin"] else get_user(g.user["username"])
        return render_template("admin_newsletter.html", issues=issues, issue=issue, subject=subject, intro=intro,
                               preview_html=preview_html, article_count=article_count, sends=sends,
                               subscriber_count=subscriber_count, already_sent=any(s["issue"] == issue for s in sends),
                               mail_ready=mailer.is_configured(), test_to=(me["email"] if me else ""),
                               csrf=csrf_token(), title="Newsletter")

    def newsletter_form():
        try:
            issue = int(request.form.get("issue") or 0)
        except ValueError:
            issue = 0
        subject = (request.form.get("subject") or "").strip()[:200] or f"The HAFS Herald — Issue #{issue}"
        intro = (request.form.get("intro") or "").strip()[:2000]
        return issue, subject, intro

    @app.post("/admin/newsletter/test")
    @require_admin
    def admin_newsletter_test():
        if not verify_csrf():
            abort(400)
        issue, subject, intro = newsletter_form()
        to = (request.form.get("test_to") or "").strip()
        back = url_for("admin_newsletter", issue=issue, subject=subject, intro=intro)
        if not is_valid_email(to):
            flash("Enter an email address for the test.", "error")
            return redirect(back)
        html, text, _ = newsletter_content(issue, subject, intro, unsubscribe_link("test"))
        try:
            mailer.send(to, "[TEST] " + subject, html, text)
            flash(f"Test sent to {to}.", "ok")
        except mailer.MailError as e:
            flash(f"Test failed: {e}", "error")
        return redirect(back)

    @app.post("/admin/newsletter/send")
    @require_admin
    def admin_newsletter_send():
        if not verify_csrf():
            abort(400)
        if not mailer.is_configured():
            flash("Email isn't set up yet (RESEND_API_KEY is missing on Railway).", "error")
            return redirect(url_for("admin_newsletter"))
        issue, subject, intro = newsletter_form()
        if not issue_articles(issue):
            flash(f"Issue #{issue} has no published articles.", "error")
            return redirect(url_for("admin_newsletter"))
        with conn() as c:
            total = c.execute("SELECT COUNT(*) FROM subscribers").fetchone()[0]
            cur = c.execute(
                "INSERT INTO newsletter_sends (issue, subject, intro, started_at, started_by, total) VALUES (?,?,?,?,?,?)",
                (issue, subject, intro, utc_now_iso(), g.user["name"], total))
            send_id = cur.lastrowid
            c.commit()
        msg = deliver(send_id)
        flash(msg, "ok" if "Done" in msg else "error")
        return redirect(url_for("admin_newsletter", issue=issue))

    @app.post("/admin/newsletter/<int:send_id>/continue")
    @require_admin
    def admin_newsletter_continue(send_id: int):
        if not verify_csrf():
            abort(400)
        msg = deliver(send_id)
        flash(msg, "ok" if "Done" in msg else "error")
        return redirect(url_for("admin_newsletter"))

    # ---------- Public: unsubscribe ----------
    @app.route("/unsubscribe", methods=["GET", "POST"])
    def unsubscribe():
        token = request.args.get("t") or request.form.get("t") or ""
        with conn() as c:
            sub = c.execute("SELECT * FROM subscribers WHERE token = ? AND token != ''", (token,)).fetchone() if token else None
            if request.method == "POST" and sub:
                # Also the target of one-click unsubscribe buttons in mail apps
                c.execute("DELETE FROM subscribers WHERE id = ?", (sub["id"],))
                c.commit()
                return render_template("unsubscribe.html", state="done", site=PUBLIC_SITE_URL)
        return render_template("unsubscribe.html", state="confirm" if sub else "unknown", token=token,
                               email=sub["email"] if sub else "", site=PUBLIC_SITE_URL)

    from media_library import bp as media_bp
    app.register_blueprint(media_bp)

    @app.post("/admin/media/upload")
    @require_login
    def admin_media_upload():
        if not verify_csrf():
            abort(400)

        # optional: allow subfolders like "covers", "inline", etc.
        subdir = (request.form.get("folder") or "").strip()
        if subdir:
            # keep it safe: only simple folder names
            if not re.match(r"^[A-Za-z0-9_-]{1,40}$", subdir):
                flash("Bad folder name.", "error")
                return redirect(url_for("media.media_index"))
            target_dir = (UPLOAD_DIR / subdir)
        else:
            target_dir = UPLOAD_DIR
        target_dir.mkdir(parents=True, exist_ok=True)

        max_mb = int(os.environ.get("MAX_UPLOAD_MB", "12"))
        max_bytes = max_mb * 1024 * 1024

        files = request.files.getlist("files")
        if not files:
            flash("No files selected.", "error")
            return redirect(url_for("media.media_index"))

        saved = 0
        skipped = 0

        for f in files:
            if not f or not getattr(f, "filename", ""):
                continue

            # size check (best-effort)
            try:
                f.stream.seek(0, os.SEEK_END)
                size = f.stream.tell()
                f.stream.seek(0)
                if size > max_bytes:
                    skipped += 1
                    continue
            except Exception:
                # if we can't measure, still attempt save
                pass

            try:
                save_upload_image(f, target_dir)
            except ValueError:
                skipped += 1
                continue
            saved += 1

        flash(f"Uploaded {saved} file(s). Skipped {skipped}.", "ok" if saved else "error")
        return redirect(url_for("media.media_index"))


    return app


# Gunicorn entrypoint (Railway needs this)
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)

