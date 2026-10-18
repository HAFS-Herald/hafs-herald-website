# The HAFS Herald — Cloudflare Pages (frontend) + Railway (backend)

This repo is split into:
- `frontend/public/` — static site for Cloudflare Pages
- `backend/` — Flask CMS/API for Railway (Admin + API + subscribers/tips + uploads)

## Backend Railway (Flask)
- **Root Directory** is `backend`.
- **Environment Variables**:
   - `HAFS_ADMIN_PASSWORD` = your admin password
   - `FLASK_SECRET` = any long random string (optional; one is generated and kept in the data folder if unset)
   - `PUBLIC_SITE_URL` = the public site's address (optional; defaults to `https://hafsherald.com`; set it only if the site moves)
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

## Email (Resend)
- Railway variables: `RESEND_API_KEY` (required to send), `MAIL_FROM` (newsletter sender, e.g.
  `The HAFS Herald <newsletter@hafsherald.com>`), `NOTIFY_FROM` (staff notifications, e.g.
  `Herald Newsroom <newsroom@hafsherald.com>`; defaults to `MAIL_FROM`), optional `MAIL_REPLY_TO` and `DIGEST_HOUR`
  (default 18, Korea time). Without the key nothing is sent and everything else works.
- **Staff notifications:** each person adds their email under **Account** and picks **Daily summary** (default: one
  email around 6 PM Korea time), **Right away**, or **Off**. Editors hear about articles submitted for review and new
  tips; writers about comments and publication. The summary leaves out review requests that were already handled.
  Admins can send summaries early from **Users → Send daily summaries now**. Summaries are triggered by the first visit
  after `DIGEST_HOUR` (the public site calls the backend on every page view).
- **Newsletter** (admins): pick an issue, add a note, preview, send a test, then send. Every email has a one-click
  unsubscribe link. Resend's free plan sends 100 emails a day; bigger lists finish with **Continue sending** the next
  day, and nobody gets an issue twice.

## Link previews (Cloudflare Pages Functions)
- `functions/_middleware.js` adds the headline, summary and cover photo to article pages so links shared on
  KakaoTalk, Instagram, Discord etc. show a proper preview. Cloudflare picks up the `functions/` folder automatically
  when the Pages project's root directory is the repo root.
- `frontend/public/_routes.json` limits it to the home and article pages.
- Optional Pages environment variable `HAFS_API_BASE` if the Railway URL changes (default is the current one).

## Admin / CMS
- Admin lives on Railway: `https://YOUR-RAILWAY.../admin`
- Sign in as `admin` with `HAFS_ADMIN_PASSWORD` (the adviser's login, and the way back in if every other account
  is locked out). Then create staff accounts under **Users**.
- Roles:
  - **Writer**: writes their own articles and submits them for review; can't publish
  - **Editor**: edits and publishes any article, reads tips, sets the current issue and staff list
  - **Admin**: everything, plus users, subscribers and backups
- When someone graduates, **Deactivate** them. Their articles stay.

## Drafts and review
- New articles start as **Draft**. Writers set **Submit for review** when ready; editors set **Published**.
- Only published articles appear on the site. Use **Preview** to check anything before it goes live.
- **Feedback:** in Preview, click 💬 under any paragraph to comment on it (or use the box for the whole article).
  Writers see open comments on their Edit page and mark them Resolved. The article list shows a 💬 count.
  Comments never appear on the public site.
- Article bodies support `**bold**`, `*italic*`, `[links](https://...)`, `## Subheadings` and `- ` lists.
- Uploaded photos are resized to 1600px and stripped of location data automatically (`MAX_IMAGE_PX` to change).
- **Autosave:** the editor keeps unsaved changes in the browser and offers to restore them after a crash or closed tab.
- **History:** every save is kept (last 50 per article, `MAX_REVISIONS`). The History page shows what changed,
  compares any version with the current text, and can restore an old version.
- **Lead story:** editors tick "Lead story" on one article per issue; it's shown large on the homepage and issue page.
  Without one, the newest article with a photo is used.
- **Author pages:** bylines link to `author.html?name=…`, listing that writer's articles (and their role from the staff list).

## Backups
- The database is copied automatically once a day to `/data/backups` (last 14 kept; change with `BACKUP_KEEP`).
- `/admin/backups` has **Back up now**, **Restore** (the current state is saved first), and **Download everything (.zip)**
  with the database and all uploaded images. Download that now and then and keep it off Railway (e.g. Google Drive).

## Spam protection
- The tip and newsletter forms have a hidden trap field for bots and allow 5 submissions per visitor per hour.
- Visitors are tracked only as a hashed ID kept for 24 hours, so tips stay anonymous.
