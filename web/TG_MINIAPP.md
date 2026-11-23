# HMF Web / Telegram Mini App

The responsive HMF web client. The same code runs as a regular website in a
browser and as a Telegram Mini App. It provides the full HMF feature set
(search, tags, details, history, settings, Rule34) plus an online reader.

Production: **https://mangafinder-zeta.vercel.app**

## Architecture

```
Browser / Telegram WebView
        │  HTTPS
        ▼
Vercel  public/     — UI static files (index.html, style.css, app.js, vanilla JS)
        │  POST /api/app
        ▼
Vercel  api/app.py  — Starlette ASGI: actions → core/ → 11 sources
        │
        ├── api/state.py — per-user history/settings (JSON in /tmp + optional KV REST)
        └── /api/cover, /api/media — image proxies with browser caching
```

The server uses the shared `core/` package: `Library`, `SearchSession`,
`Manga`/`Chapter`, the tag catalogue and all 11 sources. The
client keeps only UI state and a stable `user_id` (a Telegram ID inside
Telegram, or `crypto.randomUUID()` stored in `localStorage['hmf_client_id']`
outside Telegram).

Sign-in is mandatory: the client shows a **Sign in** dialog until a valid
session cookie is present. There is no registration; accounts are created by
the built-in `admin` account. See `web/AUTH.md`.

## Screens

| Screen | Features |
|---|---|
| **Search** | Title/author, Find, Random, Hide read, author chip. Cards with covers, badges and a context menu (details, site, copy link, similar, same author, mark as read). Infinite scroll, Found/Shown counters. |
| **Filters** | A 320 px filter sidebar; below 1000 px it becomes a left slide-out panel opened with `Tags & filters`. Tags grouped into Genres/Themes/Format/Labels, favorites on top, normalized tag filter (`full color` = `full-color` = `full colored`), included/excluded chips, AND/OR, ~70 languages, sorting, Reset + Find. Sources are configured in Settings. |
| **Details** | A separate floating window with a title bar and close button (a non-modal dialog): cover, alternative titles, badges, authors/artists, statistics, tag cloud, description, actions (site/author/similar/copy). Chapters or gallery pages are shown as a 6-column table with a language filter. |
| **Reader** | Fullscreen online reader: pages via `/api/media`, Prev/Next, chapter selector, keyboard arrows, swipe, data saver. Nothing is downloaded to the device. |
| **History** | Reading list with covers, metadata and timestamps; Details / Same author / Similar / On site / Remove actions. |
| **Settings** | Hide read, Block gay/yaoi, sources (Select all + Check sites), Local/Server data (counter, Clear history, Reset settings, Reset everything), Account sign-out, the Admin panel (admin only), and Rule34 credentials/filters. |
| **Rule34** | Web-only addition: tags with autocomplete, AI/video/image filters, infinite grid, and a viewer with video support and prev/next/Esc. |

The interface uses a 66 px header with brand and tabs, a filter sidebar,
204×388 cards, a Details window and a status bar. Responsive
behavior: at ≥1000 px the 320 px sidebar is pinned; at 700–999 px the header
uses two rows and the sidebar becomes a slide-out panel; at ≤700 px tabs scroll
horizontally, the grid has two columns and the Details window covers almost
the whole screen. There is no bottom navigation and no Mini App styling.
The Telegram SDK is only used for `ready()`/`expand()` and `openUrl()`; outside
Telegram the site behaves like a normal page.

## API

`POST /api/app` with JSON `{action, user_id?, ...}`. Main actions:

| Action | Input (besides `user_id`) | Output |
|---|---|---|
| `search` | `sources, title, author, tags, excluded, block_gay, tag_mode, languages, sort, limit` | `{items, total, has_more, states}` |
| `details` | `source, id, title` | card + `description, tags, chapters, statistics, authors, artists, alt_titles, demographic, gallery, data_saver, error?` |
| `pages` | `source, id, title, chapter_id, data_saver` | `{urls, chapter_id, page_total, error?}` |
| `random` | `sources?` | `{item}` |
| `open`, `history_add` | `source, id, title` / `item` | card / `{ok, history_count}`; writes to history |
| `history`, `history_remove`, `history_clear` | `key` | `{history}` / `{ok}` |
| `settings` | any of `sources, block_gay, hide_read, hide_read_in_search, tags, excluded, favorite_tags, tag_mode, languages, sort, rule34_*` | all fields + `history_count`; no keys means read |
| `reset_settings`, `reset_all` | — | defaults + `history_count` |
| `sources`, `tags` | — | `{sources:[{key,name,url,kind}]}`, `{tags:[{id,name,group,description}]}` |
| `check_sites` | — | `{sites:[{key,name,url,ok,detail}]}` |
| `rule34_search`, `rule34_autocomplete` | `tags/q, pid, limit, rule34_*` | `{items, has_more, pid}` / `{items}` |
| `login`, `logout`, `me` | `username, password` for login | session state / `{ok}`; the session cookie is set or cleared |
| `admin_status`, `admin_accounts`, `admin_create_account`, `admin_reset_password`, `admin_delete_account` | admin session + action fields | admin state; see `web/AUTH.md` |

Also available: `GET /api/tags`, `GET /api/cover?url=...`,
`GET /api/media?url=...`, `GET /`.

`details` may return HTTP 200 with an `error` field — this is a soft error for
chapters/statistics; the text and cover are still displayed.

## Data storage

`api/state.py` keeps up to 800 history entries, settings, and favorite/excluded
tags per `user_id` (string or number). An atomic JSON snapshot is written to
`/tmp/hmf/state.json` and loaded on startup. When `KV_REST_API_URL` and
`KV_REST_API_TOKEN` are present, per-user records are written to Vercel KV
REST (Upstash-compatible) with a 2 s timeout and a fallback to the local file.

KV is configured for the production project (`hmf-auth-kv`, Upstash for Redis,
region `fra1`). Without it, state would live only in `/tmp` and would not be
reliable across cold starts or instances.

## Project files

```
api/app.py          — Starlette ASGI: actions, proxies, static files
api/auth.py         — accounts, sessions, admin actions
api/state.py        — per-user state, history, KV/JSON
public/index.html   — markup for all screens
public/style.css    — theme and responsive rules (700 / 1000 px)
public/app.js       — all client logic (vanilla JS)
core/               — shared search/source/tag logic
requirements.txt    — starlette, requests, beautifulsoup4, certifi
vercel.json         — framework python, buildCommand, includeFiles, maxDuration: 60
```

## Deployment

```bash
npx --yes vercel@latest deploy --prod --yes
```

`vercel.json` sets `"framework": "python"`,
`"buildCommand": "pip install -r requirements.txt"`,
`functions.api/app.py.includeFiles = "public/**"` and `maxDuration: 60`
(the Hobby plan allows up to 60 s; this is needed for details and reader
pages).

## Connecting the Mini App to a Telegram bot

1. @BotFather → `/newapp` → Web App → name.
2. `/myapps` → your bot → Configure → Web App →
   `https://mangafinder-zeta.vercel.app`.
3. Inline button:

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔍 HMF", web_app=WebAppInfo(url="https://mangafinder-zeta.vercel.app"))]
])
await context.bot.send_message(chat_id=chat_id, text="🔍 HMF", reply_markup=keyboard)
```

4. `https://telegram.org/js/telegram-web-app.js` is included in `index.html`;
   `ready()` and `expand()` are called in `app.js`.

Because sign-in is mandatory, a Telegram user must sign in with a username and
password issued by the administrator before the app becomes usable.

## Known limitations

- No offline downloads: chapters are read online and cached by the browser
  (via the proxy's `Cache-Control` headers).
- `/api/cover` and `/api/media` are open proxies without a host whitelist.
- Rule34: no credentials are committed. Users can enter their own in Settings;
  the deployment can also set `R34_USER_ID` and `R34_API_KEY`.
- Sources: `nhentai.xxx` does not return tags in details; `hentaireadmoe`
  mixes in navigation tags; `check_sites` and some sources may hit the
  function timeout.
- Outside Telegram the browser `user_id` is only a local identifier; the
  server does not verify the Telegram `initData` signature (same as before).
- Search is a metasearch over site APIs/HTML parsers, not a vector search.
