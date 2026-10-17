# app.py — The HAFS Herald CMS (Flask + SQLite)
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flask_cors import CORS
from markupsafe import Markup, escape
from werkzeug.middleware.proxy_fix import ProxyFix

try:
    from PIL import Image, ImageOps
except ImportError:  # uploads still work, just without resizing
    Image = None

from flask import (
    Flask,
    abort,
    current_app,
    flash,
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

STATUSES = ("draft", "published")

# Public form limits: (max submissions, per seconds) per visitor
RATE_LIMITS = {"tip": (5, 3600), "subscribe": (5, 3600)}
MAX_TIP_CHARS = 5000
ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_IMAGE_PX = int(os.environ.get("MAX_IMAGE_PX", "1600"))

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


IMAGE_BLOCK_RE = re.compile(r'^!\[([^\]]*)\]\(([^)\s]+)(?:\s+"([^"]+)")?\)$')
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
    return Markup(f"<p>{format_inline(s)}</p>")


def require_login(fn):
    def wrapper(*args, **kwargs):
        if not session.get("authed"):
            return redirect(url_for("admin_login", next=request.path))
        return fn(*args, **kwargs)

    wrapper.__name__ = fn.__name__
    return wrapper


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
        "updated_at": r["updated_at"],
    }


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
                correction TEXT NOT NULL DEFAULT ''
            )
            """
        )
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

        # Migration: ensure 'issue' exists even if DB created earlier
        cols = [row["name"] for row in c.execute("PRAGMA table_info(articles)").fetchall()]
        if "issue" not in cols:
            c.execute("ALTER TABLE articles ADD COLUMN issue INTEGER NOT NULL DEFAULT 0")
        # Migration: articles saved before drafts existed were all live, so they stay published
        if "status" not in cols:
            c.execute("ALTER TABLE articles ADD COLUMN status TEXT NOT NULL DEFAULT 'published'")
        if "correction" not in cols:
            c.execute("ALTER TABLE articles ADD COLUMN correction TEXT NOT NULL DEFAULT ''")

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
        INSERT INTO articles (id,title,section,author,date,summary,cover,featured,issue,pullQuote,body_json,updated_at,status,correction)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
          correction=excluded.correction
        """,
        (
            (a.get("id") or "").strip(),
            (a.get("title") or "").strip(),
            (a.get("section") or "").strip(),
            (a.get("author") or "").strip(),
            (a.get("date") or "").strip(),
            (a.get("summary") or "").strip(),
            (a.get("cover") or "").strip(),
            0,
            issue_val,
            (a.get("pullQuote") or "").strip(),
            json.dumps(body_list, ensure_ascii=False),
            now,
            status,
            (a.get("correction") or "").strip(),
        ),
    )

    if owns:
        connection.commit()
        connection.close()


def delete_article(article_id: str):
    with conn() as c:
        c.execute("DELETE FROM articles WHERE id = ?", (article_id,))
        c.commit()


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
            "INSERT OR IGNORE INTO subscribers (email, created_at) VALUES (?, ?)",
            (email, now),
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
    def _daily_backup():
        maybe_daily_backup()

    @app.template_filter("timestamp_utc")
    def _timestamp_utc(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")

    @app.context_processor
    def _public_site():
        return {"public_site_url": (os.environ.get("PUBLIC_SITE_URL") or "").rstrip("/")}

    @app.get("/")
    def home():
        # The public site lives on Cloudflare Pages; set PUBLIC_SITE_URL to send visitors there.
        return redirect(os.environ.get("PUBLIC_SITE_URL") or url_for("admin_index"))

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
        articles = [row_to_article(r) for r in rows]
        return jsonify({"sections": SECTIONS, "articles": articles, "settings": get_settings()})

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
        return jsonify({"ok": True})

    # ---------- Admin auth ----------
    @app.get("/admin/login")
    def admin_login():
        return render_template("admin_login.html", next=request.args.get("next", ""), csrf=csrf_token())

    @app.post("/admin/login")
    def admin_login_post():
        if not verify_csrf():
            abort(400)
        pwd = request.form.get("password", "")
        expected = admin_password()
        if not expected:
            flash("Login is disabled until HAFS_ADMIN_PASSWORD is set on the server.", "error")
            return redirect(url_for("admin_login"))
        if secrets.compare_digest(pwd.encode(), expected.encode()):
            session["authed"] = True
            flash("Logged in.", "ok")
            return redirect(safe_next(request.form.get("next") or "/admin"))
        flash("Wrong password.", "error")
        return redirect(url_for("admin_login"))

    @app.get("/admin/logout")
    def admin_logout():
        session.pop("authed", None)
        flash("Logged out.", "ok")
        return redirect(url_for("admin_login"))

    # ---------- Admin: articles ----------
    @app.get("/admin")
    @require_login
    def admin_index():
        with conn() as c:
            rows = c.execute("SELECT * FROM articles ORDER BY date DESC").fetchall()
        articles = [row_to_article(r) for r in rows]
        return render_template("admin_list.html", articles=articles)

    @app.get("/admin/new")
    @require_login
    def admin_new():
        s = get_settings()
        a = {
            "id": "",
            "title": "",
            "section": "News",
            "author": "",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "summary": "",
            "cover": "",
            "issue": int(s.get("currentIssueNumber", 0) or 0),
            "pullQuote": "",
            "body": [],
            "status": "draft",
        }
        return render_template("admin_edit.html", a=a, sections=SECTIONS, csrf=csrf_token(), is_new=True)

    @app.get("/admin/edit/<article_id>")
    @require_login
    def admin_edit(article_id: str):
        with conn() as c:
            r = c.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if not r:
            abort(404)
        return render_template("admin_edit.html", a=row_to_article(r), sections=SECTIONS, csrf=csrf_token(), is_new=False)

    @app.get("/admin/preview/<article_id>")
    @require_login
    def admin_preview(article_id: str):
        with conn() as c:
            r = c.execute("SELECT * FROM articles WHERE id = ?", (article_id,)).fetchone()
        if not r:
            abort(404)
        a = row_to_article(r)
        blocks = [render_body_block(p) for p in a["body"]]
        return render_template("admin_preview.html", a=a, blocks=blocks, title=f"Preview: {a['title']}")

    @app.post("/admin/save")
    @require_login
    def admin_save():
        if not verify_csrf():
            abort(400)

        article_id = (request.form.get("id") or "").strip()
        if not article_id:
            flash("ID (slug) is required.", "error")
            return redirect(url_for("admin_new"))

        try:
            issue = int((request.form.get("issue") or "0").strip() or "0")
        except ValueError:
            issue = 0

        body_raw = (request.form.get("body") or "").replace("\r\n", "\n")
        body = [p.strip() for p in re.split(r"\n\s*\n", body_raw) if p.strip()]

        cover_path = (request.form.get("cover") or "").strip()

        f = request.files.get("cover_upload")
        if f and getattr(f, "filename", ""):
            try:
                out_name = save_upload_image(f, UPLOAD_DIR)
            except ValueError:
                flash("Cover must be a PNG, JPG, WEBP or GIF image.", "error")
                return redirect(url_for("admin_new"))
            cover_path = f"/assets/uploads/{out_name}"

        a = {
            "id": article_id,
            "title": (request.form.get("title") or "").strip(),
            "section": (request.form.get("section") or "News").strip(),
            "author": (request.form.get("author") or "").strip(),
            "date": (request.form.get("date") or "").strip(),
            "summary": (request.form.get("summary") or "").strip(),
            "cover": cover_path,
            "issue": issue,
            "pullQuote": (request.form.get("pullQuote") or "").strip(),
            "body": body,
            "status": request.form.get("status") if request.form.get("status") in STATUSES else "draft",
            "correction": (request.form.get("correction") or "").strip(),
        }
        if a["section"] not in SECTIONS:
            a["section"] = "News"

        upsert_article(a)
        flash("Published — it's live on the site." if a["status"] == "published" else "Saved as draft (not visible on the site).", "ok")
        return redirect(url_for("admin_edit", article_id=article_id))

    @app.post("/admin/delete/<article_id>")
    @require_login
    def admin_delete(article_id: str):
        if not verify_csrf():
            abort(400)
        delete_article(article_id)
        flash("Deleted.", "ok")
        return redirect(url_for("admin_index"))

    # ---------- Admin: subscribers & tips ----------
    @app.get("/admin/subscribers")
    @require_login
    def admin_subscribers():
        with conn() as c:
            rows = c.execute("SELECT * FROM subscribers ORDER BY created_at DESC").fetchall()
        subs = [dict(r) for r in rows]
        return render_template("admin_subscribers.html", subscribers=subs, csrf=csrf_token())

    @app.post("/admin/subscribers/delete/<int:sid>")
    @require_login
    def admin_subscribers_delete(sid: int):
        if not verify_csrf():
            abort(400)
        with conn() as c:
            c.execute("DELETE FROM subscribers WHERE id = ?", (sid,))
            c.commit()
        flash("Subscriber removed.", "ok")
        return redirect(url_for("admin_subscribers"))

    @app.get("/admin/tips")
    @require_login
    def admin_tips():
        with conn() as c:
            rows = c.execute("SELECT * FROM tips ORDER BY created_at DESC").fetchall()
        tips = [dict(r) for r in rows]
        return render_template("admin_tips.html", tips=tips, csrf=csrf_token())

    @app.post("/admin/tips/mark/<int:tid>")
    @require_login
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
        return redirect(url_for("admin_tips"))

    @app.post("/admin/tips/delete/<int:tid>")
    @require_login
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
    @require_login
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
    @require_login
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
    @require_login
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
    @require_login
    def admin_backups_create():
        if not verify_csrf():
            abort(400)
        out = make_backup("manual-" + datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S"))
        flash(f"Backup created: {out.name}", "ok")
        return redirect(url_for("admin_backups"))

    @app.get("/admin/backups/download/<name>")
    @require_login
    def admin_backups_download(name: str):
        p = backup_path(name)
        if not p:
            abort(404)
        return send_file(p, as_attachment=True, download_name=p.name)

    @app.post("/admin/backups/restore")
    @require_login
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
    @require_login
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
    @require_login
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

