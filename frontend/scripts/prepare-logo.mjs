// Deterministic preparation: never redraw or recolor the original artwork.
import sharp from "sharp";
import { fileURLToPath } from "node:url";
const source = fileURLToPath(
  new URL("../public/liquidity-radar-logo.jpg", import.meta.url),
);
const output = fileURLToPath(
  new URL("../public/liquidity-radar-logo.webp", import.meta.url),
);
const { data, info } = await sharp(source)
  .removeAlpha()
  .raw()
  .toBuffer({ resolveWithObject: true });
const rgba = Buffer.alloc(info.width * info.height * 4);
let left = info.width,
  top = info.height,
  right = 0,
  bottom = 0;
for (let y = 0; y < info.height; y++)
  for (let x = 0; x < info.width; x++) {
    const i = (y * info.width + x) * 3,
      o = (y * info.width + x) * 4;
    // Only remove near-black JPEG background; retain the dark artwork outline.
    const alpha = Math.max(data[i], data[i + 1], data[i + 2]) <= 12 ? 0 : 255;
    rgba[o] = data[i];
    rgba[o + 1] = data[i + 1];
    rgba[o + 2] = data[i + 2];
    rgba[o + 3] = alpha;
    if (alpha) {
      left = Math.min(left, x);
      right = Math.max(right, x);
      top = Math.min(top, y);
      bottom = Math.max(bottom, y);
    }
  }
const padding = 8;
left = Math.max(0, left - padding);
top = Math.max(0, top - padding);
const width = Math.min(info.width - 1, right + padding) - left + 1;
const height = Math.min(info.height - 1, bottom + padding) - top + 1;
await sharp(rgba, {
  raw: { width: info.width, height: info.height, channels: 4 },
})
  .extract({ left, top, width, height })
  .webp({ lossless: true, effort: 6 })
  .toFile(output);
console.log({ left, top, width, height, ...(await sharp(output).metadata()) });
