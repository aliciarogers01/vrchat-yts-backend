from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
import yt_dlp

app = FastAPI()

class SearchItem(BaseModel):
    id: str
    title: str
    duration: int
    thumb: str

def ydl(extra=None):
    opts = {
        "quiet": True,
        "skip_download": True,
        "nocheckcertificate": True,
        # make search lighter & more reliable
        "default_search": "ytsearch",
        "extract_flat": "in_playlist",
    }
    if extra:
        opts.update(extra)
    return yt_dlp.YoutubeDL(opts)

@app.get("/")
def root():
    return {"ok": True, "endpoints": ["/healthz", "/search", "/resolve"]}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/search")
def search(q: str = Query(..., min_length=2), max_results: int = 8):
    try:
        query = f"ytsearch{max_results}:{q}"
        with ydl() as y:
            data = y.extract_info(query, download=False)
        items = []
        for e in data.get("entries", []) or []:
            if not e: 
                continue
            items.append(SearchItem(
                id=e.get("id") or "",
                title=e.get("title") or "",
                duration=int(e.get("duration") or 0),
                thumb=e.get("thumbnail") or ""
            ).dict())
        return {"results": items}
    except Exception as ex:
        # return a readable error instead of 500
        raise HTTPException(status_code=502, detail=f"search_failed: {type(ex).__name__}: {ex}")

@app.get("/resolve")
def resolve(id: str, prefer: str = "720"):
    try:
        guard = {"quiet": True, "skip_download": True, "nocheckcertificate": True}
        url_watch = f"https://www.youtube.com/watch?v={id}"

        with ydl(guard) as y:
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

        with ydl({**guard, "format": fmt}) as y:
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
