"""Hinders Nightclub YTS tablet live search backend.

Serves the contract HindersFreeVideoTablet expects:

    GET /search?q=<query>&limit=<1-25>

returns plain text, one video per line:

    <videoId>|<title>|<channel>

The tablet parses this directly. On HTTP error statuses the tablet falls
back to its whitelisted board automatically, so a cold Render start does
not break the user experience.
"""

from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse

import ysearch

app = FastAPI(title="Hinders YTS Tablet backend")


def _clean_query(value: str) -> str:
    value = (value or "").strip()
    if "\x00" in value:
        value = value.replace("\x00", "")
    return value


@app.get("/")
def root():
    return {"ok": True, "contract": "text lines: videoId|title|channel", "endpoints": ["/healthz", "/search"]}


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/search", response_class=PlainTextResponse)
def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(6, ge=1, le=25),
):
    query = _clean_query(q)
    if not query:
        return PlainTextResponse("missing q", status_code=400)

    try:
        rows = ysearch.search(query, limit)
    except RuntimeError as ex:
        return PlainTextResponse("SEARCH UNAVAILABLE - %s" % ex, status_code=503)

    if not rows:
        return PlainTextResponse("SEARCH UNAVAILABLE - no results", status_code=503)

    return PlainTextResponse("\n".join(rows) + "\n")