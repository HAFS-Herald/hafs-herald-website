# backend/media_library.py
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import secrets
from flask import session

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, abort

try:
    from PIL import Image  # pip install Pillow
except Exception:
    Image = None

bp = Blueprint("media", __name__)

ALLOWED_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_UPLOAD_MB_DEFAULT = 12  # you can override with env var

def _get_upload_dir() -> Path:
    # app.py sets this from UPLOAD_DIR, or auto-detects the /data volume
    return Path(current_app.config["UPLOAD_DIR"]).resolve()

def _csrf_token() -> str:
    tok = session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        session["csrf"] = tok
    return tok

def _verify_csrf() -> bool:
    form_tok = request.form.get("csrf", "")
    return bool(form_tok) and secrets.compare_digest(form_tok, session.get("csrf", ""))

def _get_db_path() -> Optional[Path]:
    db = current_app.config.get("DB_PATH")
    return Path(db).resolve() if db else None

def _is_allowed_file(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in ALLOWED_EXTS

def _human_bytes(n: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1024:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}GB"

def _img_dims(p: Path) -> Tuple[Optional[int], Optional[int]]:
    if Image is None:
        return (None, None)
    try:
        with Image.open(p) as im:
            return im.size[0], im.size[1]
    except Exception:
        return (None, None)

def _scan_db_usage(filename: str) -> Optional[int]:
    """
    Tries to count occurrences of `filename` across all TEXT-ish columns in sqlite.
    If DB not configured, returns None.
    """
    db_path = _get_db_path()
    if not db_path or not db_path.exists():
        return None

    needle = f"%{filename}%"
    total = 0
    try:
        con = sqlite3.connect(str(db_path))
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        tables = [r["name"] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            cols = cur.execute(f"PRAGMA table_info({t})").fetchall()
            text_cols = [c[1] for c in cols if (c[2] or "").upper().find("TEXT") != -1 or (c[2] or "").upper().find("CHAR") != -1]
            for col in text_cols:
                try:
                    total += cur.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} LIKE ?", (needle,)).fetchone()[0]
                except Exception:
                    pass
        con.close()
        return total
    except Exception:
        return None

def admin_required(fn):
    """
    IMPORTANT: hook this into your existing admin auth.
    If you already have an auth decorator used by /admin, replace this with that.
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        if session.get("authed") is True:
            return fn(*args, **kwargs)
        return redirect(url_for("admin_login", next=request.path))
    return wrapper

@bp.get("/admin/media")
@admin_required
def media_index():
    upload_dir = _get_upload_dir()
    q = (request.args.get("q") or "").strip().lower()
    only_unused = (request.args.get("unused") == "1")

    files: List[Dict] = []
    if upload_dir.exists():
        # rglob so files uploaded into a folder (covers/, inline/, staff/) show up too
        for p in sorted(upload_dir.rglob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
            if not _is_allowed_file(p):
                continue

            rel = p.relative_to(upload_dir).as_posix()
            if q and q not in rel.lower():
                continue

            size = p.stat().st_size
            w, h = _img_dims(p)
            used = _scan_db_usage(p.name)  # None if DB not configured
            if only_unused and (used is None or used > 0):
                continue

            files.append({
                "name": rel,
                "ext": p.suffix.lower(),
                "size": size,
                "size_h": _human_bytes(size),
                "mtime": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc),
                "w": w,
                "h": h,
                "used": used,  # None = unknown
                "rel_path": f"assets/uploads/{rel}",
                "backend_path": f"/assets/uploads/{rel}",
            })

    # Simple “policy” values surfaced in UI
    max_mb = int(os.environ.get("MAX_UPLOAD_MB") or MAX_UPLOAD_MB_DEFAULT)

    return render_template(
        "admin_media.html",
        files=files,
        q=q,
        only_unused=only_unused,
        max_mb=max_mb,
        csrf=_csrf_token(),
        title="Media",
    )

@bp.post("/admin/media/delete")
@admin_required
def media_delete():
    if not _verify_csrf():
        abort(400)
    name = (request.form.get("name") or "").strip()
    if not name or "\\" in name:
        abort(400)

    upload_dir = _get_upload_dir()
    p = (upload_dir / name).resolve()
    if upload_dir not in p.parents or not _is_allowed_file(p):
        abort(400)

    if not p.exists():
        flash("File not found.", "error")
        return redirect(url_for("media.media_index"))

    p.unlink()
    flash(f"Deleted {name}", "ok")
    return redirect(url_for("media.media_index"))
