// server.js – fixed sprite-sheet endpoint + update hook
import express from "express";
import fetch from "node-fetch";
import sharp from "sharp";

const app = express();

// ---------- CONFIG ----------
const PORT = process.env.PORT || 3000;
const SHEET_COLS_DEFAULT = 3;
const SHEET_ROWS_DEFAULT = 4;

// Where your existing thumbnail endpoint lives.
// We will call: `${THUMB_ENDPOINT}?q=...&page=0&cols=3&rows=4&i=<0..11>`
const THUMB_ENDPOINT = process.env.THUMB_ENDPOINT ||
  "https://vrchat-yts-backend.onrender.com/search_grid_thumb";

// In-memory cache for the latest generated sheet
let currentSheetPng = null;      // Buffer
let currentMeta = { q: "", cols: 3, rows: 4, at: 0 };

// Small helper to fetch a single thumbnail as Buffer
async function fetchThumb(q, page, cols, rows, i) {
  const url = `${THUMB_ENDPOINT}?q=${encodeURIComponent(q)}&page=${page}&cols=${cols}&rows=${rows}&i=${i}`;
  const res = await fetch(url, { redirect: "error" });
  if (!res.ok) throw new Error(`thumb ${i} status ${res.status}`);
  const ct = res.headers.get("content-type") || "";
  if (!ct.startsWith("image/")) throw new Error(`thumb ${i} content-type ${ct}`);
  return Buffer.from(await res.arrayBuffer());
}

// Compose 3x4 sheet using sharp
async function buildSheet({ q, cols = 3, rows = 4, page = 0 }) {
  const total = cols * rows;
  // Fetch all thumbnails in parallel
  const thumbs = await Promise.all(
    Array.from({ length: total }, (_, i) => fetchThumb(q, page, cols, rows, i))
  );

  // Determine a cell size (use first image’s dimensions)
  const firstMeta = await sharp(thumbs[0]).metadata();
  const cellW = firstMeta.width || 320;
  const cellH = firstMeta.height || 180;

  const sheetW = cellW * cols;
  const sheetH = cellH * rows;

  // Compose: Top-left origin (so row 0 is the top row)
  const composites = [];
  for (let i = 0; i < total; i++) {
    const cx = i % cols;
    const cy = Math.floor(i / cols);
    const left = cx * cellW;
    const top  = cy * cellH;
    composites.push({ input: thumbs[i], left, top });
  }

  // Create transparent background and composite thumbs
  const sheet = await sharp({
      create: { width: sheetW, height: sheetH, channels: 4, background: { r: 0, g: 0, b: 0, alpha: 0 } }
    })
    .png() // base is RGBA
    .composite(composites)
    .png({ compressionLevel: 9 })
    .toBuffer();

  return { png: sheet, meta: { q, cols, rows, at: Date.now() } };
}

// ----------- ROUTES -----------

// Health ping (optional)
app.get("/health", (req, res) => res.json({ ok: true, at: Date.now() }));

// Update current sheet: /update_sheet?q=Term&cols=3&rows=4
app.get("/update_sheet", async (req, res) => {
  try {
    const q = (req.query.q || "").toString().trim();
    if (!q) return res.status(400).json({ ok: false, error: "Missing q" });

    const cols = parseInt(req.query.cols || SHEET_COLS_DEFAULT, 10) || SHEET_COLS_DEFAULT;
    const rows = parseInt(req.query.rows || SHEET_ROWS_DEFAULT, 10) || SHEET_ROWS_DEFAULT;

    const { png, meta } = await buildSheet({ q, cols, rows, page: 0 });
    currentSheetPng = png;
    currentMeta = meta;

    // Avoid caching this control response
    res.set("Cache-Control", "no-store");
    res.json({ ok: true, ...currentMeta });
  } catch (err) {
    console.error("[update_sheet] error:", err);
    res.status(500).json({ ok: false, error: String(err) });
  }
});

// Serve the latest sheet as a direct PNG (fixed URL to bake in Unity)
// GET /sheet.png
app.get("/sheet.png", (req, res) => {
  if (!currentSheetPng) {
    // If not generated yet, you can either return 503 or a 1x1 transparent PNG
    const empty = Buffer.from(
      "89504e470d0a1a0a0000000d4948445200000001000000010806000000" +
      "1f15c4890000000a49444154789c6360000002000100" +
      "05fe02fea7c6a90000000049454e44ae426082", "hex"
    );
    res.set("Content-Type", "image/png");
    res.set("Cache-Control", "no-store");
    return res.status(200).send(empty);
  }
  res.set("Content-Type", "image/png");
  res.set("Cache-Control", "no-store");        // ensure VRChat re-requests after each search
  res.set("X-Sheet-Query", currentMeta.q);
  res.set("X-Sheet-Cols", String(currentMeta.cols));
  res.set("X-Sheet-Rows", String(currentMeta.rows));
  res.send(currentSheetPng);
});

// (Optional) expose the existing thumb pass-through if you want this server to host it:
// app.get("/search_grid_thumb", ... )  // you already have this running elsewhere.

app.listen(PORT, () => {
  console.log(`[server] listening on :${PORT}`);
  console.log(`[server] sheet @ /sheet.png, update via /update_sheet?q=hello&cols=3&rows=4`);
});
