"""YouTube search for the Hinders Nightclub YTS Tablet.

Stdlib only (urllib). Serves results as plain-text lines:

    <videoId>|<title>|<channel>

which is exactly what HindersFreeVideoTablet.OnStringLoadSuccess parses.

Primary source: YouTube innertube (no API key, no quota).
Optional fallback: YouTube Data API v3 when YT_API_KEY is set.
"""

import json
import os
import urllib.parse
import urllib.request

_INNERTUBE_KEY = os.environ.get("INNERTUBE_API_KEY") or "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"

CLIENT_VERSIONS = [
    "2.20250120.10.00",
    "2.20240712.08.00",
    "2.20240101.00.00",
]

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def clean(value):
    if not value:
        return ""
    return " ".join(str(value).replace("|", " ").replace("\r", " ").replace("\n", " ").split())


def _video_title(title):
    if not title:
        return ""
    if not isinstance(title, dict):
        return clean(title)
    runs = title.get("runs")
    if runs:
        return "".join(str(r.get("text") or "") for r in runs)
    return clean(title.get("simpleText") or "")


def _extract_innertube(data, limit):
    rows = []
    try:
        contents = data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"]["sectionListRenderer"]["contents"]
    except (KeyError, TypeError):
        return rows

    for section in contents or []:
        if len(rows) >= limit:
            break
        item_section = (section or {}).get("itemSectionRenderer") or {}
        for item in item_section.get("contents") or []:
            if len(rows) >= limit:
                break
            video = (item or {}).get("videoRenderer") or {}
            video_id = video.get("videoId")
            if not video_id:
                continue
            length = (video.get("lengthText") or {}).get("simpleText") or ""
            if length == "LIVE":
                continue
            owner = (video.get("ownerText") or {}).get("runs") or [{}]
            rows.append(
                video_id
                + "|"
                + clean(_video_title(video.get("title")))
                + "|"
                + clean(owner[0].get("text") or "")
            )
    return rows


def _search_innertube(query, limit):
    last_error = None
    for client_version in CLIENT_VERSIONS:
        try:
            body = {
                "context": {
                    "client": {
                        "clientName": "WEB",
                        "clientVersion": client_version,
                        "hl": "en",
                        "gl": "US",
                        "userAgent": _UA,
                    }
                },
                "query": query,
            }
            url = "https://www.youtube.com/youtubei/v1/search?key=" + urllib.parse.quote(_INNERTUBE_KEY)
            req = urllib.request.Request(
                url,
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": _UA},
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", "ignore"))
            rows = _extract_innertube(data, limit)
            if rows:
                return rows
            last_error = RuntimeError("innertube returned no items")
        except Exception as exc:  # noqa: BLE001 - surface any source error
            last_error = exc
    raise last_error or RuntimeError("innertube failed")


def _search_data_api(query, limit):
    key = os.environ.get("YT_API_KEY")
    if not key:
        raise RuntimeError("missing YT_API_KEY")

    params = {
        "part": "snippet",
        "type": "video",
        "maxResults": str(limit),
        "q": query,
        "key": key,
    }
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))

    rows = []
    for item in data.get("items") or []:
        video_id = (item.get("id") or {}).get("videoId")
        if not video_id:
            continue
        snippet = item.get("snippet") or {}
        rows.append(
            video_id + "|" + clean(snippet.get("title") or "") + "|" + clean(snippet.get("channelTitle") or "")
        )
    return rows


def search(query, limit):
    """Return list of 'videoId|title|channel' strings or raise RuntimeError."""
    try:
        return _search_innertube(query, limit)
    except Exception as innertube_error:  # noqa: BLE001
        try:
            return _search_data_api(query, limit)
        except Exception as data_api_error:  # noqa: BLE001
            raise RuntimeError(
                "innertube: %s; data_api: %s" % (innertube_error, data_api_error)
            ) from data_api_error