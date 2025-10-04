from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import json, urllib.request, urllib.parse
import yt_dlp
import json, urllib.request, urllib.parse, random  # ← added random
import os, re, json, urllib.request, urllib.parse


app = FastAPI()

# ---------- Models ----------
class SearchItem(BaseModel):
    id: str
    title: str
    duration: int
    thumb: str

# ---------- yt-dlp base options (for /resolve) ----------
BASE_OPTS = {
    "quiet": True,
    "skip_download": True,
    "nocheckcertificate": True,
    "default_search": "ytsearch",
    "noplaylist": True,
    "geo_bypass": True,
    "geo_bypass_country": "US",
    # Pretend to be Android client to avoid web bot checks
    "extractor_args": {
        "youtube": {
            "player_client": ["android"],
            "player_skip": ["webpage"],
        }
    },
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
        )
    },
}

def ydl(extra=None):
    opts = dict(BASE_OPTS)
    if extra:
        opts.update(extra)
    return yt_dlp.YoutubeDL(opts)

# ---------- Search mirrors ----------
PIPED_BASES = [
    "https://piped.video",
    "https://pipedapi.kavin.rocks",
    "https://api-piped.mha.fi",
    "https://piped.syncpundit.io",
    "https://piped.hostux.net",
    "https://piped.astartes.nl",
    "https://piped.reallyaweso.me",
    "https://piped.lunar.icu",
]

INVIDIOUS_BASES = [
    "https://yewtu.be",
    "https://inv.nadeko.net",
    "https://invidious.projectsegfau.lt",
    "https://inv.tux.pizza",
    "https://invidious.privacydev.net",
    "https://iv.ggtyler.dev",
    "https://invidious.slipfox.xyz",
    "https://invidious.drgns.space",
]

# ISO8601 duration (e.g., PT3M33S) → seconds
_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
def _iso_to_seconds(s):
    m = _DUR_RE.fullmatch(s or "")
    if not m: return 0
    h, m_, s_ = [int(x) if x else 0 for x in m.groups()]
    return h*3600 + m_*60 + s_

# ---------- Endpoints ----------
@app.get("/")
def root():
    return {"ok": True, "endpoints": ["/healthz", "/search", "/search_debug", "/resolve"]}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/search")
def search(q: str = Query(..., min_length=2), max_results: int = 8):
    api_key = os.environ.get("YT_API_KEY")
    if not api_key:
        # key should be set in Render settings
        raise HTTPException(status_code=500, detail="missing_api_key")

    # 1) search -> get video IDs
    params = {
        "part": "snippet",
        "type": "video",
        "maxResults": str(max(1, min(max_results, 25))),
        "q": q,
        "key": api_key,
    }
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"search_http_error: {type(ex).__name__}: {ex}")

    ids = [item["id"]["videoId"] for item in data.get("items", []) if "id" in item and "videoId" in item["id"]]
    if not ids:
        return {"results": []}

    # 2) videos -> get duration + better thumbs
    params2 = {
        "part": "contentDetails,snippet",
        "id": ",".join(ids),
        "key": api_key,
    }
    url2 = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(params2)
    try:
        with urllib.request.urlopen(url2, timeout=15) as resp:
            data2 = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"videos_http_error: {type(ex).__name__}: {ex}")

    results = []
    for it in data2.get("items", []):
        vid = it.get("id") or ""
        snip = it.get("snippet") or {}
        thumbs = ((snip.get("thumbnails") or {}).get("medium") or {}).get("url") \
                 or ((snip.get("thumbnails") or {}).get("default") or {}).get("url") or ""
        dur = _iso_to_seconds((it.get("contentDetails") or {}).get("duration"))
        results.append({
            "id": vid,
            "title": snip.get("title") or "",
            "duration": int(dur),
            "thumb": thumbs
        })
    return {"results": results}

@app.get("/search_debug")
def search_debug(q: str = Query(..., min_length=2), max_results: int = 8):
    import random
    random.shuffle(PIPED_BASES)
    random.shuffle(INVIDIOUS_BASES)

    ua = {"User-Agent": "Mozilla/5.0"}
    tried = []
    # (rest of your function unchanged, but use timeout=15)

    for base in PIPED_BASES:
        url = f"{base}/api/v1/search?q={urllib.parse.quote(q)}&region=US"
        try:
            req = urllib.request.Request(url, headers=ua)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            vids = [e for e in data if isinstance(e, dict) and e.get("type") == "video"]
            return {"source": f"piped:{base}", "count": len(vids), "ok": True}
        except Exception as ex:
            tried.append({"piped": base, "error": f"{type(ex).__name__}: {ex}"})

    for base in INVIDIOUS_BASES:
        url = f"{base}/api/v1/search?q={urllib.parse.quote(q)}&type=video&region=US"
        try:
            req = urllib.request.Request(url, headers=ua)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            if isinstance(data, list):
                return {"source": f"invidious:{base}", "count": len(data), "ok": True}
        except Exception as ex:
            tried.append({"invidious": base, "error": f"{type(ex).__name__}: {ex}"})

    return {"source": "none", "count": 0, "ok": False, "tried": tried}

@app.get("/resolve")
def resolve(id: str, prefer: str = "720"):
    try:
        url_watch = f"https://www.youtube.com/watch?v={id}"

        with ydl() as y:
            info = y.extract_info(url_watch, download=False)

        if info.get("is_live"):
            return {"error": "live_not_supported"}
        if int(info.get("duration") or 0) > 7200:
            return {"error": "too_long"}

        fmt = {
            "720": "best[height<=720][ext=mp4]/best[height<=720]",
            "480": "best[height<=480][ext=mp4]/best[height<=480]",
            "audio": "bestaudio[ext=m4a]/bestaudio",
        }.get(prefer, "best[height<=720][ext=mp4]/best[height<=720]")

        with ydl({"format": fmt}) as y:
            info2 = y.extract_info(url_watch, download=False)

        url = info2.get("url")
        if not url:
            return {"error": "no_playable_url"}
        return {
            "url": url,
            "title": info2.get("title") or "",
            "duration": int(info2.get("duration") or 0),
        }
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"resolve_failed: {type(ex).__name__}: {ex}")
