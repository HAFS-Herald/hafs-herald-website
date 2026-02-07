# app.py — The HAFS Herald CMS (Flask + SQLite)
from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from flask_cors import CORS

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"

# Persist data on Railway by mounting a Volume and setting these env vars:
# DATA_DIR=/data
# DB_PATH=/data/data.sqlite3
# UPLOAD_DIR=/data/uploads
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
DB_PATH = Path(os.environ.get("DB_PATH", str(DATA_DIR / "data.sqlite3")))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(DATA_DIR / "uploads")))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
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
    "wittyQuotes": [
        "If it’s worth knowing, it’s worth verifying.",
        "The first draft lies; the second draft argues.",
        "Small campus, big consequences.",
        "We report; you decide what it means.",
        "Conversation is accountability.",
    ],
    "staff": [
        {"role": "Editor-in-Chief", "name": "Name Here", "contact": ""},
        {"role": "Managing Editor", "name": "Name Here", "contact": ""},
        {"role": "Design", "name": "Name Here", "contact": ""},
    ],
}


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
        "featured": bool(r["featured"]),
        "issue": int(r["issue"]) if "issue" in r.keys() else 0,
        "pullQuote": r["pullQuote"],
        "body": body,
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
                updated_at TEXT NOT NULL
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

        # Migration: ensure 'issue' exists even if DB created earlier
        cols = [row["name"] for row in c.execute("PRAGMA table_info(articles)").fetchall()]
        if "issue" not in cols:
            c.execute("ALTER TABLE articles ADD COLUMN issue INTEGER NOT NULL DEFAULT 0")

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
                "featured": True,
                "issue": 1,
                "pullQuote": "We don’t chase certainty; we chase clarity.",
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
    body_list = a.get("body", [])
    if not isinstance(body_list, list):
        body_list = []

    connection.execute(
        """
        INSERT INTO articles (id,title,section,author,date,summary,cover,featured,issue,pullQuote,body_json,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
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
          updated_at=excluded.updated_at
        """,
        (
            (a.get("id") or "").strip(),
            (a.get("title") or "").strip(),
            (a.get("section") or "").strip(),
            (a.get("author") or "").strip(),
            (a.get("date") or "").strip(),
            (a.get("summary") or "").strip(),
            (a.get("cover") or "").strip(),
            1 if a.get("featured") else 0,
            issue_val,
            (a.get("pullQuote") or "").strip(),
            json.dumps(body_list, ensure_ascii=False),
            now,
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
    if len(message) < 5:
        return False
    now = utc_now_iso()
    with conn() as c:
        c.execute(
            "INSERT INTO tips (message, created_at, status) VALUES (?, ?, 'new')",
            (message, now),
        )
        c.commit()
    return True


def create_app() -> Flask:
    # Serve /public as static files at the site root
    app = Flask(__name__, static_folder="public", static_url_path="")
    # Allow Cloudflare Pages (different origin) to call /api/*
    CORS(app, resources={r"/api/*": {"origins": "*"}})
    app.secret_key = os.environ.get("FLASK_SECRET", "dev-" + secrets.token_hex(16))

    init_db()

    @app.get("/")
    def home():
        return app.send_static_file("index.html")

    @app.get("/favicon.ico")
    def favicon():
        logo = PUBLIC_DIR / "assets" / "logo.png"
        if logo.exists():
            return send_from_directory(logo.parent, logo.name)
        abort(404)

    # ---------- Public API ----------
    @app.get("/api/content")
    def api_content():
        with conn() as c:
            rows = c.execute("SELECT * FROM articles ORDER BY date DESC").fetchall()
        articles = [row_to_article(r) for r in rows]
        return jsonify({"sections": SECTIONS, "articles": articles, "settings": get_settings()})

    @app.post("/api/subscribe")
    def api_subscribe():
        data = request.get_json(silent=True) or {}
        email = (data.get("email") or "").strip()
        if not add_subscriber(email):
            return jsonify({"ok": False, "error": "Please enter a valid email."}), 400
        return jsonify({"ok": True})

    @app.post("/api/tip")
    def api_tip():
        data = request.get_json(silent=True) or {}
        msg = (data.get("message") or "").strip()
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
        expected = os.environ.get("HAFS_ADMIN_PASSWORD", "changeme")
        if pwd == expected:
            session["authed"] = True
            flash("Logged in.", "ok")
            nxt = request.form.get("next") or "/admin"
            return redirect(nxt)
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

    @app.post("/admin/seed")
    @require_login
    def admin_seed():
        if not verify_csrf():
            abort(400)
        seed_articles_if_empty()
        flash("Seeded (if empty).", "ok")
        return redirect(url_for("admin_index"))

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
            "featured": False,
            "issue": int(s.get("currentIssueNumber", 0) or 0),
            "pullQuote": "",
            "body": [],
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

    @app.post("/admin/save")
    @require_login
    def admin_save():
        if not verify_csrf():
            abort(400)

        article_id = (request.form.get("id") or "").strip()
        if not article_id:
            flash("ID (slug) is required.", "error")
            return redirect(url_for("admin_new"))

        featured = request.form.get("featured") == "on"
        try:
            issue = int((request.form.get("issue") or "0").strip() or "0")
        except ValueError:
            issue = 0

        body_raw = (request.form.get("body") or "").replace("\r\n", "\n")
        body = [p.strip() for p in re.split(r"\n\s*\n", body_raw) if p.strip()]

        cover_path = (request.form.get("cover") or "").strip()

        f = request.files.get("cover_upload")
        if f and getattr(f, "filename", ""):
            filename = safe_filename(f.filename)
            stem, _, ext = filename.partition(".")
            token = secrets.token_hex(6)
            out_name = f"{stem}_{token}{('.' + ext) if ext else ''}"
            out_path = UPLOAD_DIR / out_name
            f.save(out_path)
            cover_path = f"assets/uploads/{out_name}"

        a = {
            "id": article_id,
            "title": (request.form.get("title") or "").strip(),
            "section": (request.form.get("section") or "News").strip(),
            "author": (request.form.get("author") or "").strip(),
            "date": (request.form.get("date") or "").strip(),
            "summary": (request.form.get("summary") or "").strip(),
            "cover": cover_path,
            "featured": featured,
            "issue": issue,
            "pullQuote": (request.form.get("pullQuote") or "").strip(),
            "body": body,
        }
        if a["section"] not in SECTIONS:
            a["section"] = "News"

        upsert_article(a)
        flash("Saved.", "ok")
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
        quotes_text = "\n".join(settings.get("wittyQuotes", []))
        return render_template(
            "admin_settings.html",
            settings=settings,
            quotes_text=quotes_text,
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

        quotes_text = (request.form.get("wittyQuotes") or "").replace("\r\n", "\n")
        settings["wittyQuotes"] = [q.strip() for q in quotes_text.split("\n") if q.strip()]

        staff_text = (request.form.get("staff") or "").replace("\r\n", "\n")
        staff = []
        for line in [l.strip() for l in staff_text.split("\n") if l.strip()]:
            parts = [p.strip() for p in line.split("—")]
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

    @app.get("/admin/export.json")
    @require_login
    def admin_export():
        with conn() as c:
            rows = c.execute("SELECT * FROM articles ORDER BY date DESC").fetchall()
            subs = c.execute("SELECT * FROM subscribers ORDER BY created_at DESC").fetchall()
            tips = c.execute("SELECT * FROM tips ORDER BY created_at DESC").fetchall()
        articles = [row_to_article(r) for r in rows]
        return jsonify(
            {
                "sections": SECTIONS,
                "articles": articles,
                "settings": get_settings(),
                "subscribers": [dict(r) for r in subs],
                "tips": [dict(r) for r in tips],
            }
        )

    return app


# Gunicorn entrypoint (Railway needs this)
app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)

