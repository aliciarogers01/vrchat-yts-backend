import { DurableObject } from "cloudflare:workers";
import { MEDIA_SLOTS, multiSearch, publicHttps } from "./media-sources.js";

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
const SLOT_COUNT = 256;
const SLOT_TTL = 900;

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
        signal: AbortSignal.timeout(4000),
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

      rows.push({
        id: video.videoId,
        title: clean(videoTitle(video.title)),
        channel: clean(
          (video.ownerText &&
            video.ownerText.runs &&
            video.ownerText.runs[0] &&
            video.ownerText.runs[0].text) ||
            ""
        ),
      });
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
        signal: AbortSignal.timeout(4000),
      });
      if (!res.ok) throw new Error("invidious HTTP " + res.status);
      const arr = await res.json();
      if (!Array.isArray(arr)) throw new Error("invidious bad payload");
      const rows = arr
        .slice(0, limit)
        .map((v) => ({
          id: clean(v.videoId),
          title: clean(v.title),
          channel: clean(v.author),
        }))
        .filter((row) => row.id);
      if (rows.length > 0) return rows;
      lastError = new Error("invidious returned no items");
    } catch (err) {
      lastError = err;
    }
  }
  throw lastError || new Error("invidious failed");
}

async function doSearch(query, limit, source, apiKey, invidiousInstances, env) {
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

  return { rows, errors };
}

export class SlotStore extends DurableObject {
  async allocateMedia(rows) {
    const now=Date.now(); const records=await this.ctx.storage.list({prefix:"m:"});const updates={};const output=[];
    for(const row of rows){
      let slot=-1;
      for(let i=0;i<MEDIA_SLOTS;i++){const r=records.get("m:"+i);if(r&&r.id===row.id&&r.url===row.url){slot=i;break;}}
      if(slot<0)for(let i=0;i<MEDIA_SLOTS;i++)if(!records.has("m:"+i)){slot=i;break;}
      // Slots are immutable: queued/saved links must never resolve to a different item.
      if(slot<0)break;
      const record={...row,expires:now+7*86400000};records.set("m:"+slot,record);updates["m:"+slot]=record;output.push({slot,...row});
    }
    if(output.length)await this.ctx.storage.put(updates);
    return output;
  }
  async lookupMedia(slot){const r=await this.ctx.storage.get("m:"+slot);return r&&r.expires>Date.now()?r:null;}
  async allocate(rows) {
    const now = Date.now();
    const cursorValue = await this.ctx.storage.get("cursor");
    let cursor = Number.isInteger(cursorValue) ? cursorValue : 0;
    const records = await this.ctx.storage.list({ prefix: "s:" });
    const updates = {};
    const output = [];

    for (const row of rows) {
      let slot = -1;
      for (let offset = 0; offset < SLOT_COUNT; offset++) {
        const index = (cursor + offset) % SLOT_COUNT;
        const existing = records.get("s:" + index);
        if (!existing || existing.expires <= now || existing.v === row.id) {
          slot = index;
          break;
        }
      }

      if (slot < 0) slot = cursor % SLOT_COUNT;
      cursor = (slot + 1) % SLOT_COUNT;

      const record = {
        v: row.id,
        t: row.title,
        c: row.channel,
        expires: now + SLOT_TTL * 1000,
      };
      updates["s:" + slot] = record;
      records.set("s:" + slot, record);
      output.push({ slot, ...row });
    }

    updates.cursor = cursor;
    await this.ctx.storage.put(updates);
    return output;
  }

  async lookup(slot) {
    const key = "s:" + slot;
    const record = await this.ctx.storage.get(key);
    if (!record || record.expires <= Date.now()) {
      if (record) await this.ctx.storage.delete(key);
      return null;
    }
    return record;
  }
}

async function allocateSlotsKv(env, rows) {
  if (!env || !env.SLOTS) {
    return rows.map((row, index) => ({ slot: index, ...row }));
  }

  const kv = env.SLOTS;
  let cursor = 0;
  try {
    cursor = parseInt((await kv.get("cursor")) || "0", 10) || 0;
  } catch (err) {}

  const output = [];
  for (const row of rows) {
    let slot = -1;
    for (let offset = 0; offset < SLOT_COUNT; offset++) {
      const index = (cursor + offset) % SLOT_COUNT;
      const key = "s:" + index;
      let existing = null;
      try {
        existing = await kv.get(key);
      } catch (err) {}

      if (!existing) {
        slot = index;
        break;
      }

      try {
        if (JSON.parse(existing).v === row.id) {
          slot = index;
          break;
        }
      } catch (err) {}
    }

    if (slot < 0) slot = cursor % SLOT_COUNT;
    cursor = (slot + 1) % SLOT_COUNT;

    try {
      await kv.put(
        "s:" + slot,
        JSON.stringify({ v: row.id, t: row.title, c: row.channel }),
        { expirationTtl: SLOT_TTL }
      );
    } catch (err) {}

    output.push({ slot, ...row });
  }

  try {
    await kv.put("cursor", String(cursor));
  } catch (err) {}
  return output;
}

async function lookupSlotKv(env, slot) {
  if (!env || !env.SLOTS) return null;
  try {
    const value = await env.SLOTS.get("s:" + slot);
    return value ? JSON.parse(value) : null;
  } catch (err) {
    return null;
  }
}

async function allocateSlots(env, rows) {
  if (env && env.SLOT_STORE) {
    try {
      return await env.SLOT_STORE.getByName("hinders-yts-slots").allocate(rows);
    } catch (err) {
      console.error(JSON.stringify({ event: "slot_allocate_fallback", error: String(err) }));
    }
  }
  return allocateSlotsKv(env, rows);
}

async function lookupSlot(env, slot) {
  if (env && env.SLOT_STORE) {
    try {
      const record = await env.SLOT_STORE.getByName("hinders-yts-slots").lookup(slot);
      if (record) return record;
    } catch (err) {
      console.error(JSON.stringify({ event: "slot_lookup_fallback", error: String(err) }));
    }
  }
  return lookupSlotKv(env, slot);
}

async function thumbnailResponse(videoId) {
  const imageUrl = "https://i.ytimg.com/vi/" + videoId + "/mqdefault.jpg";
  const upstream = await fetch(imageUrl, {
    headers: { "User-Agent": "HindersNightclub/1.0" },
        signal: AbortSignal.timeout(4000),
  });
  if (!upstream.ok) return plain("THUMBNAIL UNAVAILABLE", upstream.status);

  const headers = new Headers(upstream.headers);
  headers.set("Content-Type", upstream.headers.get("Content-Type") || "image/jpeg");
  headers.set("Cache-Control", "public, max-age=86400");
  headers.set("Access-Control-Allow-Origin", "*");
  return new Response(upstream.body, { status: 200, headers });
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
    const path = url.pathname;

    if (path === "/" || path === "/test" || path === "/health") {
      return plain(
        "Hinders YTS search backend OK\nusage: /search?q=<query>&limit=<1-20>  /wsearch?q=&limit=  /yt/<slot>  /tn/<slot>"
      );
    }

    if(path==="/msearch"){
      if(request.method!=="GET")return plain("METHOD NOT ALLOWED",405);
      const query=(url.searchParams.get("q")||"").trim();const category=url.searchParams.get("category")||"all";
      if(!query||query.length>120||!["all","youtube","movies","audio","radio"].includes(category))return plain("INVALID SEARCH",400);
      const limit=Math.max(1,Math.min(20,parseInt(url.searchParams.get("limit")||"20",10)||20));
      try{
        const result=await multiSearch(query,category,limit,async(q,n)=>{
          const r=await doSearch(q,n,"auto",apiKey,invidiousInstances,env);if(!r.rows.length)throw new Error("YouTube search unavailable");return r.rows;
        });
        if(!result.rows.length)return plain(result.unavailable.length?"SEARCH UNAVAILABLE - TRY ANOTHER SOURCE":"NO RESULTS",result.unavailable.length?503:200);
        const rows=await env.SLOT_STORE.getByName("hinders-media-slots-v1").allocateMedia(result.rows);
        if(!rows.length)return plain("MEDIA CATALOG FULL",503);
        return plain(rows.map(r=>r.slot+"|"+r.id+"|"+r.title+"|"+r.channel).join("\n")+"|END\n");
      }catch(e){console.error(JSON.stringify({event:"media_search_failed",message:String(e)}));return plain("SEARCH UNAVAILABLE - TRY AGAIN",503);}
    }
    const mediaMatch=path.match(/^\/media\/(play|thumb)\/(\d+)$/);
    if(mediaMatch){
      const slot=Number(mediaMatch[2]);if(slot<0||slot>=MEDIA_SLOTS)return plain("INVALID SLOT",400);
      const row=await env.SLOT_STORE.getByName("hinders-media-slots-v1").lookupMedia(slot);
      if(!row)return plain("RESULT EXPIRED - SEARCH AGAIN",404);
      if(mediaMatch[1]==="play"){if(!publicHttps(row.url))return plain("UNSUPPORTED URL",400);return Response.redirect(row.url,302);}
      if(!row.thumb)return plain("NO THUMBNAIL",404);
      const thumb=publicHttps(row.thumb);if(!thumb||!["archive.org","i.ytimg.com"].includes(thumb.hostname))return plain("NO THUMBNAIL",404);
      const r=await fetch(thumb,{signal:AbortSignal.timeout(8000)});if(!r.ok)return plain("NO THUMBNAIL",404);
      return new Response(r.body,{headers:{"Content-Type":r.headers.get("Content-Type")||"image/jpeg","Cache-Control":"public, max-age=3600"}});
    }
    const source = url.searchParams.get("source") || "auto";

    if (path === "/search" || path === "/wsearch") {
      const query = clean(url.searchParams.get("q"));
      if (!query) return plain("missing q", 400);

      const rawLimit = parseInt(url.searchParams.get("limit") || "6", 10);
      const limit = Math.min(Math.max(isNaN(rawLimit) ? 6 : rawLimit, 1), 20);
      const { rows, errors } = await doSearch(
        query,
        limit,
        source,
        apiKey,
        invidiousInstances,
        env
      );

      if (rows.length === 0) {
        return plain(
          "SEARCH UNAVAILABLE - " + (errors.join("; ") || "no search source"),
          503
        );
      }

      if (path === "/wsearch") {
        const slotted = await allocateSlots(env, rows);
        return plain(
          slotted
            .map(
              (row) =>
                row.slot +
                "|" +
                row.id +
                "|" +
                row.title +
                "|" +
                row.channel
            )
            .join("\n") + "|END\n"
        );
      }

      return plain(
        rows.map((row) => row.id + "|" + row.title + "|" + row.channel).join("\n") +
          "\n"
      );
    }

    const videoMatch = path.match(/^\/yt\/([0-9]+)$/);
    if (videoMatch) {
      const record = await lookupSlot(env, videoMatch[1]);
      if (!record || !record.v) return plain("SLOT EXPIRED", 404);
      return Response.redirect("https://www.youtube.com/watch?v=" + record.v, 302);
    }

    const thumbnailMatch = path.match(/^\/tn\/([0-9]+)$/);
    if (thumbnailMatch) {
      const record = await lookupSlot(env, thumbnailMatch[1]);
      if (!record || !record.v) return plain("SLOT EXPIRED", 404);
      return thumbnailResponse(record.v);
    }

    return plain("NOT FOUND", 404);
  },
};
