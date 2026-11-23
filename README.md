# HMF Web (Vercel edition)

The web version of HMF. It uses the shared `core/` package, plus a Starlette
backend and a vanilla JS/CSS frontend.

## What is inside

- `api/` — backend: `app.py` (JSON API + static files), `auth.py` (accounts,
  sessions, admin panel), `state.py` (history and settings in KV).
- `core/` — site search, source adapters, tags and data models.
- `public/` — UI: Search, Details, Reader, History, Settings and Rule34.
- `web/` — notes on the Telegram Mini App and authentication.
- `vercel.json` — Vercel config (Python, `maxDuration: 60`).

## Deploying to Vercel

1. Import the repository into Vercel. Framework preset: **Other/Python**,
   Root Directory: `.`.
2. Build command: `pip install -r requirements.txt` (already in `vercel.json`).
3. Create an Upstash for Redis store in Storage and connect it to the project.
   Vercel will add these automatically:
   - `KV_REST_API_URL`
   - `KV_REST_API_TOKEN`
   - `KV_REST_API_READ_ONLY_TOKEN`
   - `KV_URL`
   - `REDIS_URL`
4. Deploy.

Without KV the app still works anonymously, but sign-in is unavailable.

## Sign-in

The **Sign in** dialog appears as soon as the site opens. There is no
registration in the UI.

```text
admin / nimda
```

The `admin` account is created automatically. In `Settings → Admin` you can
create and delete user accounts and reset passwords.

See `web/AUTH.md` for details.

## Important

This repository is private on purpose: the source contains a hardcoded
`admin/nimda` pair. Before making the repository public, move it into an
environment variable. Rule34 credentials are not committed here: users enter
their own in Settings, or the deployment supplies `R34_USER_ID` and
`R34_API_KEY` as environment variables.

## Local development

```powershell
pip install -r requirements.txt
npx vercel dev
```

To test authentication locally, provide `KV_REST_API_URL` and
`KV_REST_API_TOKEN` in your Vercel environment.
