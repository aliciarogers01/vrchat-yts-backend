from fastapi import FastAPI, Query
from pydantic import BaseModel
import yt_dlp

app = FastAPI()

class SearchItem(BaseModel):
    id: str
    title: str
    duration: int
    thumb: str

def ydl():
    return yt_dlp.YoutubeDL({
        "quiet": True,
        "skip_download": True,
        "nocheckcertificate": True
    })

@app.get("/search")
def search(q: str = Query(..., min_length=2), max_results: int = 8):
    query = f"ytsearch{max_results}:{q}"
    with ydl() as y:
        data = y.extract_info(query, download=False)
    items = []
    for e in data["entries"]:
        if not e: continue
        dur = int(e.get("duration") or 0)
        items.append(SearchItem(
            id=e["id"],
            title=e.get("title",""),
            duration=dur,
            thumb=(e.get("thumbnail") or "")
        ).dict())
    return {"results": items}

@app.get("/resolve")
def resolve(id: str, prefer: str = "720"):
    fmt_map = {
        "720": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720]",
        "480": "best[height<=480]",
        "audio": "bestaudio[ext=m4a]/bestaudio"
    }
    yopts = {
        "quiet": True,
        "skip_download": True,
        "nocheckcertificate": True,
        "format": fmt_map.get(prefer, fmt_map["720"])
    }
    with yt_dlp.YoutubeDL(yopts) as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={id}", download=False)
        url = info.get("url")
        title = info.get("title","")
        dur = int(info.get("duration") or 0)
    return {"url": url, "title": title, "duration": dur}

@app.get("/subs")
def subs(id: str, lang: str = "en"):
    with ydl() as y:
        info = y.extract_info(f"https://www.youtube.com/watch?v={id}", download=False)
        tracks = info.get("subtitles") or info.get("automatic_captions") or {}
        best = tracks.get(lang) or next(iter(tracks.values()), [])
        if not best:
            return {"vtt": ""}
        vtt_url = next((x["url"] for x in best if x.get("ext") == "vtt"), best[0]["url"])
    return {"vtt_url": vtt_url}
