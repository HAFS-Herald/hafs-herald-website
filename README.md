# The HAFS Herald — Cloudflare Pages (frontend) + Railway (backend)

This repo is split into:
- `frontend/public/` — static site for Cloudflare Pages
- `backend/` — Flask CMS/API for Railway (Admin + API + subscribers/tips + uploads)

## Backend Railway (Flask)
- **Root Directory** is `backend`.
- **Environment Variables**:
   - `HAFS_ADMIN_PASSWORD` = your admin password
   - `FLASK_SECRET` = any long random string
1**Volume** mounted at `/data`.
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

