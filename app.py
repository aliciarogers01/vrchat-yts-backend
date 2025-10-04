from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import json, urllib.request, urllib.parse
import yt_dlp

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
]
INVIDIOUS_BASES = [
    "https://yewtu.be",
    "https://inv.nadeko.net",
    "https://invidious.projectsegfau.lt",
]

# ---------- Endpoints ----------
@app.get("/")
def root():
    return {"ok": True, "endpoints": ["/healthz", "/search", "/search_debug", "/resolve"]}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/search")
def search(q: str = Query(..., min_length=2), max_results: int = 8):
    """
    Searches via public mirrors (Piped first, then Invidious) and maps to our schema.
    Returns empty list if all mirrors fail—never 500.
    """
    ua = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        )
    }

    # Try Piped
    for base in PIPED_BASES:
        try:
            url = f"{base}/api/v1/search?q={urllib.parse.quote(q)}&region=US"
            req = urllib.request.Request(url, headers=ua)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            vids = [e for e in data if isinstance(e, dict) and e.get("type") == "video"]
            if vids:
                out = []
                for v in vids[:max_results]:
                    out.append(SearchItem(
                        id=v.get("id") or "",
                        title=v.get("title") or "",
                        duration=int(v.get("duration") or 0),
                        thumb=(v.get("thumbnail") or v.get("thumbnailUrl") or "")
                    ).dict())
                return {"results": out}
        except Exception:
            continue  # try next mirror

    # Try Invidious
    for base in INVIDIOUS_BASES:
        try:
            url = f"{base}/api/v1/search?q={urllib.parse.quote(q)}&type=video&region=US"
            req = urllib.request.Request(url, headers=ua)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            if isinstance(data, list) and data:
                out = []
                for v in data[:max_results]:
                    if not isinstance(v, dict):
                        continue
                    thumbs = v.get("videoThumbnails") or []
                    thumb = ""
                    if isinstance(thumbs, list) and thumbs:
                        thumb = (thumbs[-1].get("url") or thumbs[0].get("url") or "")
                    out.append(SearchItem(
                        id=v.get("videoId") or "",
                        title=v.get("title") or "",
                        duration=int(v.get("lengthSeconds") or 0),
                        thumb=thumb
                    ).dict())
                if out:
                    return {"results": out}
        except Exception:
            continue

    return {"results": []}

@app.get("/search_debug")
def search_debug(q: str = Query(..., min_length=2), max_results: int = 8):
    """Small debug tool to see which mirror answers."""
    ua = {"User-Agent": "Mozilla/5.0"}
    tried = []

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
