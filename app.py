from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse, Response, JSONResponse
from pydantic import BaseModel
import os, re, io, json, random, urllib.request, urllib.parse
from PIL import Image, ImageDraw, ImageFont
import yt_dlp

app = FastAPI()

# --------------------------- Models ---------------------------
class SearchItem(BaseModel):
    id: str
    title: str
    duration: int
    thumb: str

# -------------------- yt-dlp base options ---------------------
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

# ------------------ Helpers / Utilities -----------------------
UA = {"User-Agent": "Mozilla/5.0"}

def _urlopen(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout)

# ISO8601 duration → seconds
_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
def _iso_to_seconds(s: str) -> int:
    m = _DUR_RE.fullmatch(s or "")
    if not m:
        return 0
    h, m_, s_ = [int(x) if x else 0 for x in m.groups()]
    return h * 3600 + m_ * 60 + s_

def _thumb_url(yt_id: str, quality: str = "hq") -> str:
    # valid: default (120x90), mq (320x180), hq (480x360), sd (640x480), max (1280x720)
    q = (quality or "hq").lower()
    name = {
        "default": "default.jpg",
        "mq": "mqdefault.jpg",
        "hq": "hqdefault.jpg",
        "sd": "sddefault.jpg",
        "max": "maxresdefault.jpg",
    }.get(q, "hqdefault.jpg")
    return f"https://i.ytimg.com/vi/{yt_id}/{name}"

def _playable_url_from_id(yt_id: str, prefer: str = "720") -> str | None:
    """Return a direct HLS/MP4 URL for a YouTube ID (or None on failure)."""
    url_watch = f"https://www.youtube.com/watch?v={yt_id}"
    with ydl() as y:
        info = y.extract_info(url_watch, download=False)

    if info.get("is_live"):
        return None
    if int(info.get("duration") or 0) > 7200:
        return None

    fmt = {
        "720": "best[height<=720][ext=mp4]/best[height<=720]",
        "480": "best[height<=480][ext=mp4]/best[height<=480]",
        "audio": "bestaudio[ext=m4a]/bestaudio",
    }.get(prefer, "best[height<=720][ext=mp4]/best[height<=720]")

    with ydl({"format": fmt}) as y:
        info2 = y.extract_info(url_watch, download=False)

    return info2.get("url")

# --------------------------- Endpoints ------------------------
@app.get("/")
def root():
    return {"ok": True, "endpoints": ["/healthz", "/search", "/search_grid_thumb", "/thumb", "/resolve", "/resolve_index", "/search_grid"]}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/search")
def search(q: str = Query(..., min_length=2), max_results: int = 8):
    """YouTube Data API search → returns ids, titles, durations, and DIRECT thumbnail URLs (i.ytimg.com)."""
    api_key = os.environ.get("YT_API_KEY")
    if not api_key:
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
        with _urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"search_http_error: {type(ex).__name__}: {ex}")

    ids = [item["id"]["videoId"] for item in data.get("items", []) if item.get("id", {}).get("videoId")]
    if not ids:
        return {"results": []}

    # 2) videos -> get duration + thumbs (these URLs are already i.ytimg.com)
    params2 = {
        "part": "contentDetails,snippet",
        "id": ",".join(ids),
        "key": api_key,
    }
    url2 = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(params2)
    try:
        with _urlopen(url2, timeout=15) as resp:
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

@app.get("/thumb")
def thumb(id: str, quality: str = "hq", mode: str = "bytes"):
    """
    Simple thumbnail fetch by video id.
    mode=bytes (default): proxy image bytes (best for VRChat ImageDownloader).
    mode=redirect: 302 to i.ytimg.com URL (some VRChat versions may not follow).
    """
    url = _thumb_url(id, quality=quality)
    if mode == "redirect":
        return RedirectResponse(url=url, status_code=302)
    try:
        with _urlopen(url, timeout=15) as resp:
            data = resp.read()
        return Response(content=data, media_type="image/jpeg", headers={
            "Cache-Control": "public, max-age=3600"
        })
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"thumb_fetch_failed: {type(ex).__name__}: {ex}")

@app.get("/search_grid_thumb")
def search_grid_thumb(
    q: str = Query(..., min_length=2),
    page: int = 0,
    cols: int = 3,
    rows: int = 4,
    i:   int = 0,
    quality: str = "hq",
    mode: str = "bytes"  # "bytes" (recommended) or "redirect"
):
    """
    Return the thumbnail for the i-th cell in a (cols x rows) page of results as an IMAGE.
    This endpoint now returns actual JPEG bytes by default (works with VRCImageDownloader).
    """
    # how many results needed for this page:
    per_page = max(1, cols * rows)
    # fetch enough to cover pages up to 'page'
    need = (page + 1) * per_page

    data = search(q=q, max_results=min(need, 25))  # YT API caps at 50; we're using <=25
    results = data.get("results", [])

    # compute absolute index for this page
    idx = page * per_page + i
    if idx < 0 or idx >= len(results):
        raise HTTPException(status_code=404, detail="index_out_of_range")

    vid = results[idx]["id"]
    url = _thumb_url(vid, quality=quality)

    if mode == "redirect":
        return RedirectResponse(url=url, status_code=302)

    try:
        with _urlopen(url, timeout=15) as resp:
            img_bytes = resp.read()
        return Response(content=img_bytes, media_type="image/jpeg", headers={
            "Cache-Control": "public, max-age=900"
        })
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"thumb_proxy_failed: {type(ex).__name__}: {ex}")

@app.get("/resolve")
def resolve(id: str, prefer: str = "720"):
    """Resolve a YouTube ID to a playable media URL using yt-dlp (JSON response)."""
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

@app.get("/resolve_index")
def resolve_index(q: str = Query(..., min_length=2), page: int = 0, i: int = 0, prefer: str = "720"):
    """
    Picks result i from the requested page, resolves it to a direct media URL,
    and 302-redirects there so the VRChat video player can load it.
    """
    per_page = 10
    need = (page + 1) * per_page
    data = search(q=q, max_results=min(need, 25))
    results = data.get("results", [])
    idx = page * per_page + i
    if not results or idx < 0 or idx >= len(results):
        raise HTTPException(status_code=404, detail="index_out_of_range")

    yt_id = results[idx]["id"]
    media = _playable_url_from_id(yt_id, prefer=prefer)
    if not media:
        raise HTTPException(status_code=502, detail="no_playable_url")

    return RedirectResponse(url=media, status_code=302)

@app.get("/search_grid")
def search_grid(q: str = Query(..., min_length=2), page: int = 0):
    """
    Renders a simple PNG grid (2x5 by default) with indices + titles from /search results.
    Useful for debugging.
    """
    data = search(q=q, max_results=10)
    results = data.get("results", [])[:10]

    # Layout
    cols, rows = 2, 5
    cell_w, cell_h = 640, 360
    pad = 20
    W = cols * cell_w + (cols + 1) * pad
    H = rows * cell_h + (rows + 1) * pad

    img = Image.new("RGB", (W, H), (18, 18, 18))
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
        small = ImageFont.truetype("DejaVuSans.ttf", 18)
    except Exception:
        font = small = ImageFont.load_default()

    for n, item in enumerate(results):
        c, r = n % cols, n // cols
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + pad)

        draw.rectangle([x, y, x + cell_w, y + cell_h], outline=(80, 80, 80), width=2)
        draw.rectangle([x, y, x + 54, y + 36], fill=(0, 123, 255))
        draw.text((x + 10, y + 8), f"{n}", fill="white", font=font)

        title = (item.get("title") or "")[:60]
        draw.text((x + 8, y + cell_h - 40), title, fill=(230, 230, 230), font=small)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


