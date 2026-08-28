// Hinders Nightclub - YTS Tablet YouTube search backend (Path A)
//
// Deploy for free on Cloudflare Workers:
//   npm i -g wrangler
//   wrangler login
//   wrangler deploy cloudflare-worker.js --name hinders-ytsearch
//
// Resulting URL:
//   https://hinders-ytsearch.<your-subdomain>.workers.dev/search?q=artist+song&limit=6
//
// Output format is plain text, one video per line:
//   <videoId>|<title>|<channel>
// which is exactly what HindersFreeVideoTablet.OnStringLoadSuccess parses.
//
// Environment variables (optional via wrangler secret / vars):
//   INNERTUBE_API_KEY   - override the public YouTube innertube key (default below)
//   INVIDIOUS_INSTANCES - comma-separated list of Invidious API hosts used as a
//                         fallback when YouTube innertube returns nothing, e.g.
//                         "https://invidious.example.com,https://inv-2.example.org"

const DEFAULT_INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8";
const CLIENT_VERSIONS = [
  "2.20250120.10.00",
  "2.20240712.08.00",
  "2.20240101.00.00",
];

function clean(value) {
  if (!value) return "";
  return String(value).replace(/[\r\n|]/g, " ").trim();
}

function videoTitle(title) {
  if (!title) return "";
  if (Array.isArray(title.runs)) return title.runs.map((r) => r.text || "").join("");
  if (title.simpleText) return title.simpleText;
  return "";
}

async function searchInnertube(query, limit, apiKey, env) {
  let lastError = null;
  const configuredVersion = (env && env.WEB_CLIENT_VERSION) || "";
  const versions = configuredVersion
    ? [configuredVersion]
    : CLIENT_VERSIONS;

  for (const clientVersion of versions) {
    try {
      const body = {
        context: {
          client: {
            clientName: "WEB",
            clientVersion,
            hl: "en",
            gl: "US",
            userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
          },
        },
        query,
      };

      const url =
        "https://www.youtube.com/youtubei/v1/search?key=" +
        encodeURIComponent(apiKey);
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error("innertube HTTP " + res.status);

      const data = await res.json();
      const rows = extractInnertubeRows(data, limit);
      if (rows.length > 0) return rows;
      lastError = new Error("innertube returned no items");
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error("innertube failed");
}

function extractInnertubeRows(data, limit) {
  const rows = [];
  const contents =
    data &&
    data.contents &&
    data.contents.twoColumnSearchResultsRenderer &&
    data.contents.twoColumnSearchResultsRenderer.primaryContents &&
    data.contents.twoColumnSearchResultsRenderer.primaryContents
      .sectionListRenderer &&
    data.contents.twoColumnSearchResultsRenderer.primaryContents
      .sectionListRenderer.contents;

  if (!contents) return rows;

  for (const section of contents) {
    if (rows.length >= limit) break;
    const items = (section && section.itemSectionRenderer && section.itemSectionRenderer.contents) || [];
    for (const item of items) {
      if (rows.length >= limit) break;
      const video = item && item.videoRenderer;
      if (!video || !video.videoId) continue;
      if (video.lengthText && video.lengthText.simpleText === "LIVE") continue;

      rows.push(
        video.videoId +
          "|" +
          clean(videoTitle(video.title)) +
          "|" +
          clean((video.ownerText && video.ownerText.runs && video.ownerText.runs[0] && video.ownerText.runs[0].text) || "")
      );
    }
  }
  return rows;
}

async function searchInvidious(query, limit, instances) {
  const list = (instances || "")
    .split(",")
    .map((s) => s.trim().replace(/\/+$/, ""))
    .filter(Boolean);
  if (list.length === 0) throw new Error("no Invidious instances configured");

  let lastError = null;
  for (const instance of list) {
    try {
      const url =
        instance + "/api/v1/search?q=" + encodeURIComponent(query) + "&type=video";
      const res = await fetch(url, {
        headers: { "User-Agent": "HindersNightclub/1.0" },
      });
      if (!res.ok) throw new Error("invidious HTTP " + res.status);
      const arr = await res.json();
      if (!Array.isArray(arr)) throw new Error("invidious bad payload");
      const rows = arr
        .slice(0, limit)
        .map((v) => clean(v.videoId) + "|" + clean(v.title) + "|" + clean(v.author))
        .filter((row) => !row.startsWith("|"));
      if (rows.length > 0) return rows;
      lastError = new Error("invidious returned no items");
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error("invidious failed");
}

function plain(text, status) {
  return new Response(text, {
    status: status || 200,
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}

export default {
  async fetch(request, env) {
    const apiKey = (env && env.INNERTUBE_API_KEY) || DEFAULT_INNERTUBE_KEY;
    const invidiousInstances = (env && env.INVIDIOUS_INSTANCES) || "";

    const url = new URL(request.url);
    const source = url.searchParams.get("source") || "auto";

    if (url.pathname === "/" || url.pathname === "/test" || url.pathname === "/health") {
      return plain("Hinders YTS search backend OK\nusage: /search?q=<query>&limit=<1-20>");
    }

    if (url.pathname !== "/search") {
      return plain("NOT FOUND", 404);
    }

    const query = clean(url.searchParams.get("q"));
    if (!query) return plain("missing q", 400);

    const rawLimit = parseInt(url.searchParams.get("limit") || "6", 10);
    const limit = Math.min(Math.max(isNaN(rawLimit) ? 6 : rawLimit, 1), 20);

    const errors = [];
    let rows = [];

    if (source === "auto" || source === "innertube") {
      try {
        rows = await searchInnertube(query, limit, apiKey, env);
      } catch (err) {
        errors.push("innertube: " + err.message);
      }
    }

    if (rows.length === 0 && (source === "auto" || source === "invidious")) {
      try {
        rows = await searchInvidious(query, limit, invidiousInstances);
      } catch (err) {
        errors.push("invidious: " + err.message);
      }
    }

    if (rows.length === 0) {
      return plain("SEARCH UNAVAILABLE - " + (errors.join("; ") || "no search source"), 503);
    }

    return plain(rows.join("\n") + "\n");
  },
};