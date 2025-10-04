from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import yt_dlp
import json, urllib.request, urllib.parse

import json, urllib.request, urllib.parse  # (keep if already added)

PIPED_BASES = [
    "https://piped.video",
    "https://pipedapi.kavin.rocks",
    "https://api-piped.mha.fi",
]
INVIDIOUS_BASES = [
    "https://yewtu.be",
    "https://inv.nadeko.net",
    "https://invidious.projectsegfau.lt",
]

@app.get("/search_debug")
def search_debug(q: str = Query(..., min_length=2), max_results: int = 8):
    """Debug search that shows which mirror responded and what it returned."""
    headers = {"User-Agent": "Mozilla/5.0"}
    tried = []

    # try piped
    for base in PIPED_BASES:
        url = f"{base}/api/v1/search?q={urllib.parse.quote(q)}&region=US"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            videos = [e for e in data if isinstance(e, dict) and e.get("type") == "video"]
            return {
                "source": f"piped:{base}",
                "count": len(videos),
                "sample": [v.get("title") for v in videos[:5]],
            }
        except Exception as ex:
            tried.append({"piped": base, "error": f"{type(ex).__name__}: {ex}"})

    # try invidious
    for base in INVIDIOUS_BASES:
        url = f"{base}/api/v1/search?q={urllib.parse.quote(q)}&type=video&region=US"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            if isinstance(data, list) and data:
                return {
                    "source": f"invidious:{base}",
                    "count": len(data),
                    "sample": [v.get("title") for v in data[:5] if isinstance(v, dict)],
                }
        except Exception as ex:
            tried.append({"invidious": base, "error": f"{type(ex).__name__}: {ex}"})

    return {"source": "none", "count": 0, "tried": tried}


app = FastAPI()

class SearchItem(BaseModel):
    id: str
    title: str
    duration: int
    thumb: str

BASE_OPTS = {
    "quiet": True,
    "skip_download": True,
    "nocheckcertificate": True,
    "default_search": "ytsearch",
    "extract_flat": False,
    "noplaylist": True,
    "geo_bypass": True,
    "geo_bypass_country": "US",
    # Pretend to be Android client (helps avoid “sign in to confirm you’re not a bot”)
    "extractor_args": {
        "youtube": {
            "player_client": ["android"],
            "player_skip": ["webpage"]
        }
    },
    "http_headers": {
        "User-Agent": "Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
    },
}

def ydl(extra=None):
    opts = BASE_OPTS.copy()
    if extra:
        opts.update(extra)
    return yt_dlp.YoutubeDL(opts)

@app.get("/")
def root():
    return {"ok": True, "endpoints": ["/healthz", "/search", "/resolve"]}

@app.get("/healthz")
def healthz():
    return {"ok": True}

# Use Piped (YouTube mirror API) for reliable search, then map to our schema
PIPED_BASES = [
    "https://piped.video",
    "https://pipedapi.kavin.rocks",
    "https://api-piped.mha.fi",
]

@app.get("/search")
def search(q: str = Query(..., min_length=2), max_results: int = 8):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    }
    for base in PIPED_BASES:
        try:
            url = f"{base}/api/v1/search?q={urllib.parse.quote(q)}&region=US"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))

            videos = [e for e in data if isinstance(e, dict) and e.get("type") == "video"]
            items = []
            for v in videos[:max_results]:
                items.append(SearchItem(
                    id=v.get("id") or "",
                    title=v.get("title") or "",
                    duration=int(v.get("duration") or 0),
                    thumb=(v.get("thumbnail") or v.get("thumbnailUrl") or "")
                ).dict())
            return {"results": items}
        except Exception:
            # Try next mirror if this one fails
            continue

    # If all mirrors failed, return empty list (no 500)
    return {"results": []}

@app.get("/resolve")
def resolve(id: str, prefer: str = "720"):
    try:
        url_watch = f"https://www.youtube.com/watch?v={id}"

        # First pass (Android client)
        with ydl({"extract_flat": False}) as y:
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

