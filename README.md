# The HAFS Herald — Cloudflare Pages (frontend) + Railway (backend)

This repo is split into:
- `frontend/public/` — static site for Cloudflare Pages
- `backend/` — Flask CMS/API for Railway (Admin + API + subscribers/tips + uploads)

## Deploy Backend to Railway (Flask)
1. Push this repo to GitHub.
2. In Railway: **New Project → Deploy from GitHub**.
3. Set **Root Directory** to `backend`.
4. Add **Environment Variables**:
   - `HAFS_ADMIN_PASSWORD` = your admin password
   - `FLASK_SECRET` = any long random string
5. (Recommended) Add a **Volume** and mount it at `/data`.
   Then set env vars:
   - `DATA_DIR=/data`
   - `DB_PATH=/data/data.sqlite3`
   - `UPLOAD_DIR=/data/uploads`
6. Deploy. Your backend URL will look like: `https://<name>.up.railway.app`
7. Test:
   - `https://<railway>/api/content`
   - `https://<railway>/admin`

## Deploy Frontend to Cloudflare Pages
1. In Cloudflare: **Workers & Pages → Create application → Pages**.
2. Connect your GitHub repo.
3. Build settings:
   - Framework preset: **None**
   - Build command: *(leave blank)*
   - Output directory: `frontend/public`
4. Deploy. You’ll get a Pages URL like `https://<project>.pages.dev`.

## Connect Frontend → Backend
1. Edit `frontend/public/config.js` and set:
   ```js
   window.HAFS_API_BASE = "https://YOUR-RAILWAY-APP.up.railway.app";
   ```
2. Redeploy Pages (push commit).

## Admin / CMS
- Admin lives on Railway: `https://YOUR-RAILWAY.../admin`
- Subscribers: `/admin/subscribers`
- Tips inbox: `/admin/tips`

## Local dev
- Frontend: open `frontend/public/index.html` with a local static server (or just use the backend locally).
- Backend: run `backend/run_windows.bat` (Windows) or `python backend/app.py`.

## Uploads / Images (why your pictures weren’t loading)
- Cover uploads are stored on the **backend** (Railway) in `UPLOAD_DIR` (ideally on a Railway Volume).
- Articles store image paths like `/assets/uploads/<filename>`.
- The backend now serves those files at `GET /assets/uploads/<filename>`.
- The frontend normalizes media URLs when it loads `/api/content`, so images render correctly even when the site is hosted on Cloudflare Pages.
- `frontend/public/_redirects` also adds a safety redirect so visiting `https://hafsherald.com/assets/uploads/...` will send you to the backend.

