# The HAFS Herald — Cloudflare Pages (frontend) + Railway (backend)

This repo is split into:
- `frontend/public/` — static site for Cloudflare Pages
- `backend/` — Flask CMS/API for Railway (Admin + API + subscribers/tips + uploads)

## Backend Railway (Flask)
- **Root Directory** is `backend`.
- **Environment Variables**:
   - `HAFS_ADMIN_PASSWORD` = your admin password
   - `FLASK_SECRET` = any long random string (optional; one is generated and kept in the data folder if unset)
   - `PUBLIC_SITE_URL` = your Cloudflare Pages address (optional; the backend home page and "View site" go there)
- **Volume** mounted at `/data`, with
   env vars:
   - `DATA_DIR=/data`
   - `DB_PATH=/data/data.sqlite3`
   - `UPLOAD_DIR=/data/uploads`

## Frontend Cloudflare Pages
-Build settings:
   - Framework preset: **None**
   - Build command: *(leave blank)*
   - Output directory: `frontend/public`

## Admin / CMS
- Admin lives on Railway: `https://YOUR-RAILWAY.../admin`
- Subscribers: `/admin/subscribers`
- Tips inbox: `/admin/tips`


## Drafts
- New articles start as **Draft** and are only visible in admin. Use **Preview** to check them.
- Set Status to **Published** and save to put an article on the site.

## Backups
- The database is copied automatically once a day to `/data/backups` (last 14 kept; change with `BACKUP_KEEP`).
- `/admin/backups` has **Back up now**, **Restore** (the current state is saved first), and **Download everything (.zip)**
  with the database and all uploaded images. Download that now and then and keep it off Railway (e.g. Google Drive).

## Spam protection
- The tip and newsletter forms have a hidden trap field for bots and allow 5 submissions per visitor per hour.
- Visitors are tracked only as a hashed ID kept for 24 hours, so tips stay anonymous.
