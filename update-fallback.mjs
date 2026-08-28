// Hinders Nightclub - YTS Tablet fallback board updater (Path B)
//
// Runs with Node 18+ (uses built-in fetch). No dependencies.
// Reads ./queries.json, searches YouTube for each query, and writes a
// single flat file: ./results.txt
//
//   results.txt format:
//     # comment lines start with '#' and are ignored by the tablet
//     <videoId>|<title>|<channel>
//
// Publish results.txt on a VRChat-whitelisted host so it loads for EVERY
// visitor without the "Allow Untrusted URLs" setting:
//   - GitHub Pages: https://<user>.github.io/<repo>/results.txt   (*.github.io)
//   - Pastebin raw: https://pastebin.com/raw/<key>                (pastebin.com)
//   - DisBridge:    https://*.disbridge.com/...                   (*.disbridge.com)
//
// Run manually:   node update-fallback.mjs
// or schedule it with the included GitHub Actions workflow.

import { readFileSync, writeFileSync } from "node:fs";

const DEFAULT_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8";
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

async function search(query, limit, apiKey) {
  let lastError = null;
  for (const clientVersion of CLIENT_VERSIONS) {
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
        "https://www.youtube.com/youtubei/v1/search?key=" + encodeURIComponent(apiKey);
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();

      const rows = [];
      const contents =
        data &&
        data.contents &&
        data.contents.twoColumnSearchResultsRenderer &&
        data.contents.twoColumnSearchResultsRenderer.primaryContents &&
        data.contents.twoColumnSearchResultsRenderer.primaryContents.sectionListRenderer &&
        data.contents.twoColumnSearchResultsRenderer.primaryContents.sectionListRenderer.contents;

      if (contents) {
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
      }
      if (rows.length > 0) return rows;
      lastError = new Error("no items");
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error("search failed");
}

async function main() {
  const config = JSON.parse(
    readFileSync(new URL("./queries.json", import.meta.url), "utf8")
  );
  const apiKey = process.env.INNERTUBE_API_KEY || DEFAULT_KEY;
  const queries = (config.queries || []).slice(0, 6);
  const perQuery = Math.max(1, Math.min(parseInt(config.limitPerQuery || 3, 10), 10));

  if (queries.length === 0) {
    console.error("No queries in queries.json — nothing to do.");
    process.exit(1);
  }

  const lines = [
    "# Hinders Nightclub - curated fallback board",
    "# generated " + new Date().toISOString() + " - update via queries.json",
    "# format: videoId|title|channel",
    "",
  ];

  for (const q of queries) {
    const query = clean(q);
    if (!query) continue;
    console.log("Searching: " + query);
    try {
      const rows = await search(query, perQuery, apiKey);
      lines.push("== " + query + " ==");
      lines.push(...rows);
      lines.push("");
    } catch (err) {
      console.warn("  failed: " + err.message);
    }
  }

  writeFileSync(new URL("./results.txt", import.meta.url), lines.join("\n"), "utf8");
  console.log("Wrote results.txt with " + lines.length + " lines.");
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});