from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import RedirectResponse, Response
from pydantic import BaseModel
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

from PIL import Image
import yt_dlp

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
    "noplaylist": True,
    "geo_bypass": True,
    "geo_bypass_country": "US",
    "extractor_args": {"youtube": {"player_client": ["android"], "player_skip": ["webpage"]}},
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


UA = {"User-Agent": "Mozilla/5.0"}
_DUR_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")
_TRANSPARENT_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c636000000200010005fe02fea7c6a90000000049454e44ae426082"
)

LAST_Q = "hello"
LAST_PAGE = 0
COLS = 3
ROWS = 4
_current_sheet_png: Optional[bytes] = None
_current_meta = {"q": LAST_Q, "cols": COLS, "rows": ROWS, "page": LAST_PAGE, "at": 0}


def _urlopen(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout)


def _iso_to_seconds(s: str) -> int:
    m = _DUR_RE.fullmatch(s or "")
    if not m:
        return 0
    h, m_, s_ = [int(x) if x else 0 for x in m.groups()]
    return h * 3600 + m_ * 60 + s_


def _thumb_url(yt_id: str, quality: str = "hq") -> str:
    name = {
        "default": "default.jpg",
        "mq": "mqdefault.jpg",
        "hq": "hqdefault.jpg",
        "sd": "sddefault.jpg",
        "max": "maxresdefault.jpg",
    }.get((quality or "hq").lower(), "hqdefault.jpg")
    return f"https://i.ytimg.com/vi/{yt_id}/{name}"


def _safe_grid(cols: int, rows: int) -> Tuple[int, int, int]:
    cols = max(1, min(int(cols or 3), 5))
    rows = max(1, min(int(rows or 4), 5))
    return cols, rows, cols * rows


def set_state(q: Optional[str] = None, page: Optional[int] = None):
    global LAST_Q, LAST_PAGE
    if q is not None:
        LAST_Q = q
    if page is not None:
        LAST_PAGE = max(0, int(page))


def _youtube_search(q: str, max_results: int = 12):
    api_key = os.environ.get("YT_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="missing_api_key")

    max_results = max(1, min(max_results, 25))
    params = {
        "part": "snippet",
        "type": "video",
        "maxResults": str(max_results),
        "q": q,
        "key": api_key,
    }
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
    try:
        with _urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"search_http_error: {type(ex).__name__}: {ex}")

    ids = [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]
    if not ids:
        return []

    params2 = {"part": "contentDetails,snippet", "id": ",".join(ids), "key": api_key}
    url2 = "https://www.googleapis.com/youtube/v3/videos?" + urllib.parse.urlencode(params2)
    try:
        with _urlopen(url2, timeout=15) as resp:
            data2 = json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"videos_http_error: {type(ex).__name__}: {ex}")

    out = []
    for it in data2.get("items", []):
        vid = it.get("id") or ""
        snip = it.get("snippet") or {}
        thumbs = snip.get("thumbnails") or {}
        thumb = (thumbs.get("medium") or {}).get("url") or (thumbs.get("default") or {}).get("url") or _thumb_url(vid)
        dur = _iso_to_seconds((it.get("contentDetails") or {}).get("duration"))
        out.append({"id": vid, "title": snip.get("title") or "", "duration": int(dur), "thumb": thumb})
    return out


def _grid_item(q: str, page: int, i: int, cols: int, rows: int):
    cols, rows, per_page = _safe_grid(cols, rows)
    page = max(0, int(page or 0))
    i = int(i or 0)
    need = (page + 1) * per_page
    results = _youtube_search(q=q, max_results=min(need, 25))
    idx = page * per_page + i
    if idx < 0 or idx >= len(results):
        raise HTTPException(status_code=404, detail="index_out_of_range")
    set_state(q=q, page=page)
    return results[idx]


def _playable_url_from_id(yt_id: str, prefer: str = "720") -> Optional[str]:
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


def _fetch_image(url: str) -> Image.Image:
    with _urlopen(url, timeout=15) as resp:
        data = resp.read()
    img = Image.open(io.BytesIO(data))
    return img.convert("RGB")


def _build_sheet(results: List[dict], cols: int, rows: int, page: int) -> Tuple[bytes, Tuple[int, int]]:
    cols, rows, per_page = _safe_grid(cols, rows)
    start = page * per_page
    picks = results[start:start + per_page]
    if len(picks) < per_page:
        raise HTTPException(status_code=404, detail="not_enough_results")

    imgs = [_fetch_image(item.get("thumb") or _thumb_url(item["id"], "mq")) for item in picks]
    w, h = imgs[0].size
    imgs = [im if im.size == (w, h) else im.resize((w, h), Image.LANCZOS) for im in imgs]

    sheet = Image.new("RGB", (w * cols, h * rows))
    for n, im in enumerate(imgs):
        sheet.paste(im, ((n % cols) * w, (n // cols) * h))

    buf = io.BytesIO()
    sheet.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), (w, h)


def _rebuild_sheet(q: str, page: int, cols: int, rows: int) -> Tuple[bytes, Tuple[int, int]]:
    cols, rows, per_page = _safe_grid(cols, rows)
    need = (page + 1) * per_page
    results = _youtube_search(q=q, max_results=min(need, 25))
    return _build_sheet(results, cols, rows, page)


@app.get("/")
def root():
    return {
        "ok": True,
        "endpoints": [
            "/healthz",
            "/search",
            "/search_grid_thumb",
            "/resolve_index",
            "/update_sheet",
            "/sheet.png",
        ],
    }


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/search")
def search(q: str = Query(..., min_length=2), cols: int = 3, rows: int = 4, page: int = 0):
    cols, rows, per_page = _safe_grid(cols, rows)
    set_state(q=q, page=page)
    results = _youtube_search(q=q, max_results=min((page + 1) * per_page, 25))
    return {"results": results[page * per_page:page * per_page + per_page]}


@app.get("/thumb")
def thumb(id: str, quality: str = "hq", mode: str = "bytes"):
    url = _thumb_url(id, quality=quality)
    if mode == "redirect":
        return RedirectResponse(url=url, status_code=302)
    try:
        with _urlopen(url, timeout=15) as resp:
            data = resp.read()
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=900"})
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"thumb_fetch_failed: {type(ex).__name__}: {ex}")


@app.get("/search_grid_thumb")
def search_grid_thumb(
    q: str = Query(..., min_length=2),
    page: int = 0,
    cols: int = 3,
    rows: int = 4,
    i: int = 0,
    quality: str = "hq",
):
    item = _grid_item(q=q, page=page, i=i, cols=cols, rows=rows)
    url = item.get("thumb") or _thumb_url(item["id"], quality=quality)
    try:
        with _urlopen(url, timeout=15) as resp:
            data = resp.read()
        return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=900"})
    except Exception as ex:
        raise HTTPException(status_code=502, detail=f"thumb_fetch_failed: {type(ex).__name__}: {ex}")


@app.get("/resolve_index")
def resolve_index(
    q: str = Query(..., min_length=2),
    page: int = 0,
    i: int = 0,
    prefer: str = "720",
    cols: int = 3,
    rows: int = 4,
):
    item = _grid_item(q=q, page=page, i=i, cols=cols, rows=rows)
    media = _playable_url_from_id(item["id"], prefer=prefer)
    if not media:
        raise HTTPException(status_code=502, detail="no_playable_url")
    return RedirectResponse(url=media, status_code=302)


@app.get("/update_sheet")
def update_sheet(
    q: Optional[str] = Query(None, min_length=2),
    page: Optional[int] = None,
    cols: Optional[int] = None,
    rows: Optional[int] = None,
):
    global COLS, ROWS, _current_sheet_png, _current_meta
    if q is not None:
        set_state(q=q)
    if page is not None:
        set_state(page=page)
    if cols is not None:
        COLS = int(cols)
    if rows is not None:
        ROWS = int(rows)
    COLS, ROWS, _ = _safe_grid(COLS, ROWS)

    png, _ = _rebuild_sheet(LAST_Q, LAST_PAGE, COLS, ROWS)
    _current_sheet_png = png
    _current_meta = {"q": LAST_Q, "cols": COLS, "rows": ROWS, "page": LAST_PAGE, "at": int(time.time() * 1000)}
    return Response(content=_TRANSPARENT_PNG, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/sheet.png")
def sheet_png():
    global _current_sheet_png
    if _current_sheet_png is None:
        try:
            png, _ = _rebuild_sheet(LAST_Q, LAST_PAGE, COLS, ROWS)
            _current_sheet_png = png
        except Exception:
            return Response(content=_TRANSPARENT_PNG, media_type="image/png", headers={"Cache-Control": "no-store"})

    return Response(
        content=_current_sheet_png,
        media_type="image/png",
        headers={
            "Cache-Control": "no-store",
            "X-Sheet-Query": LAST_Q,
            "X-Sheet-Cols": str(COLS),
            "X-Sheet-Rows": str(ROWS),
            "X-Sheet-Page": str(LAST_PAGE),
        },
    )
