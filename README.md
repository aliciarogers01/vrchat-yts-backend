# vrchat-yts-backend

Free, no-API-key backend for the **Hinders Nightclub YTS Tablet**.

The tablet currently behaves like this:

- **Live search** (optional): `GET /search?q=<query>&limit=6` must return
  **plain-text** lines `<videoId>|<title>|<channel>`.
- **Fallback board** (always available): a whitelisted static `results.txt`
  served from GitHub Pages loads for every visitor with zero settings.

This repo powers both.

---

## Live search

### Render (free) - `app.py` + `ysearch.py`

`app.py` is a FastAPI service that answers the tablet's exact search contract.

- Search source: YouTube innertube (unauthenticated, no quota) in `ysearch.py`.
- Optional fallback: set `YT_API_KEY` (YouTube Data API v3) env var; it is used
  automatically if innertube is blocked.
- No `yt-dlp`, no Pillow, no paid plan needed. Render free sleeps after ~15 min
  idle; the first request after sleep is slow, but the tablet auto-falls back
  to the board if the download fails, so nothing breaks.

Deploy: push to `main`, then create a Render Blueprint (or point Render at this
repo) using `render.yaml` (runtime: python, plan: free).

Test:
```
https://<your-service>.onrender.com/search?q=the+weeknd&limit=6
```

### Cloudflare Worker (alternative, free) - `cloudflare-worker.js`

Same contract, different host. Requires a free Cloudflare account:
```
npm i -g wrangler
wrangler login
wrangler deploy cloudflare-worker.js --name hinders-ytsearch
```
Result: `https://hinders-ytsearch.<your-subdomain>.workers.dev`

### Whitelist caveat (live search)

`.onrender.com` and `*.workers.dev` are **not** on VRChat's string-loading
allow list, so visitors need *Allow Untrusted URLs* enabled (or the host added
to the world's 10-host allow list if your SDK exposes it). The static board has
no such requirement.

### Tablet config

`Hinders Nightclub > YTS Tablet > Configure Search Backend...`

- Live search: `https://<your-service>.onrender.com` (no trailing slash)
- Fallback board: `https://aliciarogers01.github.io/vrchat-yts-backend/results.txt`

---

## Fallback board (works for everyone)

A flat file of curated results served through GitHub Pages on a whitelisted host.

Board URL:

```
https://aliciarogers01.github.io/vrchat-yts-backend/results.txt
```

### How the board updates

- `update-fallback.mjs` (Node 18+, no dependencies) searches YouTube and rewrites
  `results.txt` in `videoId|title|channel` format.
- `.github/workflows/update-fallback.yml` regenerates it on a schedule (every 6
  hours), on push, or manually (Actions > **Update Fallback Results** > Run workflow).
- Edit `queries.json` to change what gets searched. Up to 6 queries,
  `limitPerQuery` results each.

Run locally: `node update-fallback.mjs`.

### Enabling GitHub Pages (one-time)

1. Repo **Settings > Pages**.
2. Source: **Deploy from a branch**, branch `main`, folder `/`.
3. After the next push the board is live at the URL above.

---

## Files

- `app.py` / `ysearch.py` - live search (Render free, tablet contract).
- `cloudflare-worker.js` - live search (Cloudflare Worker, tablet contract).
- `update-fallback.mjs` / `queries.json` - board generator.
- `.github/workflows/update-fallback.yml` - scheduled board refresh.
- `results.txt` - generated board.