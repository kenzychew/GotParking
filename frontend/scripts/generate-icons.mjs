// Generates the PWA icon PNGs (public/icons/*.png) with zero image-library
// dependencies -- just Node's built-in zlib deflate and a hand-rolled PNG
// encoder. Run via `npm run icons`. Re-run any time the brand color or mark
// changes; the output is committed (no build-time dependency on this
// script), so the PNGs must be regenerated and re-committed together.
import { writeFileSync, mkdirSync } from "node:fs";
import { deflateSync } from "node:zlib";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const outDir = join(__dirname, "..", "public", "icons");
mkdirSync(outDir, { recursive: true });

// ---------------------------------------------------------------------------
// Minimal PNG encoder (8-bit RGBA, filter type 0 / none, zlib via node:zlib)
// ---------------------------------------------------------------------------
const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    }
    table[n] = c >>> 0;
  }
  return table;
})();

function crc32(buf) {
  let crc = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    crc = CRC_TABLE[(crc ^ buf[i]) & 0xff] ^ (crc >>> 8);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

function pngChunk(type, data) {
  const typeBuf = Buffer.from(type, "ascii");
  const lenBuf = Buffer.alloc(4);
  lenBuf.writeUInt32BE(data.length, 0);
  const crcBuf = Buffer.alloc(4);
  crcBuf.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])), 0);
  return Buffer.concat([lenBuf, typeBuf, data, crcBuf]);
}

function encodePng(width, height, rgbaPixels) {
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  const ihdrData = Buffer.alloc(13);
  ihdrData.writeUInt32BE(width, 0);
  ihdrData.writeUInt32BE(height, 4);
  ihdrData[8] = 8; // bit depth
  ihdrData[9] = 6; // color type: RGBA
  ihdrData[10] = 0; // compression
  ihdrData[11] = 0; // filter
  ihdrData[12] = 0; // interlace
  const ihdr = pngChunk("IHDR", ihdrData);

  const stride = width * 4;
  const raw = Buffer.alloc((stride + 1) * height);
  for (let y = 0; y < height; y++) {
    const rowStart = y * (stride + 1);
    raw[rowStart] = 0; // filter type: none
    rgbaPixels.copy(raw, rowStart + 1, y * stride, y * stride + stride);
  }
  const idat = pngChunk("IDAT", deflateSync(raw));
  const iend = pngChunk("IEND", Buffer.alloc(0));
  return Buffer.concat([signature, ihdr, idat, iend]);
}

// ---------------------------------------------------------------------------
// Tiny raster drawing helpers
// ---------------------------------------------------------------------------
function setPixel(buf, size, x, y, [r, g, b, a]) {
  if (x < 0 || y < 0 || x >= size || y >= size) return;
  const idx = (y * size + x) * 4;
  buf[idx] = r;
  buf[idx + 1] = g;
  buf[idx + 2] = b;
  buf[idx + 3] = a;
}

function fillRect(buf, size, x0, y0, w, h, color) {
  for (let y = y0; y < y0 + h; y++) {
    for (let x = x0; x < x0 + w; x++) {
      setPixel(buf, size, x, y, color);
    }
  }
}

// Blocky 5-wide x 7-tall bitmap "P" (parking mark) -- simple, legible at
// small sizes, and cheap to scale by an integer factor.
const GLYPH_P = ["11110", "10001", "10001", "11110", "10000", "10000", "10000"];

function drawGlyph(buf, size, glyph, scale, color) {
  const glyphW = glyph[0].length * scale;
  const glyphH = glyph.length * scale;
  const startX = Math.round((size - glyphW) / 2);
  const startY = Math.round((size - glyphH) / 2);
  for (let row = 0; row < glyph.length; row++) {
    for (let col = 0; col < glyph[row].length; col++) {
      if (glyph[row][col] === "1") {
        fillRect(buf, size, startX + col * scale, startY + row * scale, scale, scale, color);
      }
    }
  }
}

const BRAND_BG = [9, 105, 218, 255]; // #0969da -- matches the CSS accent token
const WHITE = [255, 255, 255, 255];

function makeIcon(size, { paddingRatio = 0.12 } = {}) {
  const pixels = Buffer.alloc(size * size * 4);
  fillRect(pixels, size, 0, 0, size, size, BRAND_BG);
  const usable = size * (1 - paddingRatio * 2);
  const scale = Math.max(1, Math.floor(usable / GLYPH_P.length));
  drawGlyph(pixels, size, GLYPH_P, scale, WHITE);
  return pixels;
}

const targets = [
  { file: "pwa-192.png", size: 192, paddingRatio: 0.12 },
  { file: "pwa-512.png", size: 512, paddingRatio: 0.12 },
  // Maskable icons get cropped to a circle/rounded-square by the OS, so the
  // glyph needs a bigger safe-zone margin (~ the standard 20% each side).
  { file: "maskable-512.png", size: 512, paddingRatio: 0.22 },
];

for (const { file, size, paddingRatio } of targets) {
  const pixels = makeIcon(size, { paddingRatio });
  const png = encodePng(size, size, pixels);
  writeFileSync(join(outDir, file), png);
  console.log(`wrote ${join("public", "icons", file)} (${size}x${size})`);
}
