import type {BillboardImage} from "@/viewport/api/billboards";

// Factorio-style icons for the type-icon overlay, as billboard images: each is a small canvas painted
// once and shared by every icon of that look (the image's key names the look). Plain canvas drawing, so
// either viewport can show them.

const CANVAS = 128;

function icon(key: string, paint: (ctx: CanvasRenderingContext2D, s: number) => void): BillboardImage {
  return {
    key: `type-icon:${key}`,
    untagged: true,
    draw: () => {
      const canvas = document.createElement("canvas");
      canvas.width = CANVAS;
      canvas.height = CANVAS;
      paint(canvas.getContext("2d")!, CANVAS);
      return canvas;
    },
  };
}

function roundedDisk(
  ctx: CanvasRenderingContext2D,
  s: number,
  fill: string,
  stroke = "#ffffff",
) {
  ctx.beginPath();
  ctx.arc(s / 2, s / 2, s * 0.42, 0, Math.PI * 2);
  ctx.fillStyle = fill;
  ctx.fill();
  ctx.lineWidth = s * 0.06;
  ctx.strokeStyle = stroke;
  ctx.stroke();
}

function centeredGlyph(
  ctx: CanvasRenderingContext2D,
  s: number,
  glyph: string,
  color: string,
) {
  ctx.fillStyle = color;
  ctx.font = `bold ${s * 0.5}px sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(glyph, s / 2, s * 0.54);
}

/** A colored disk with a glyph/letter — used for equipment (P pump, T tank,
 * ⚡ electrical, ⚙ other). */
export function diskImage(glyph: string, bg: string, fg = "#ffffff"): BillboardImage {
  return icon(`disk:${glyph}:${bg}:${fg}`, (ctx, s) => {
    roundedDisk(ctx, s, bg);
    centeredGlyph(ctx, s, glyph, fg);
  });
}

/** A colored teardrop — a fluid medium marker (blue water, black oil). */
export function dropImage(color: string): BillboardImage {
  return icon(`drop:${color}`, (ctx, s) => {
    const cx = s / 2;
    ctx.beginPath();
    ctx.moveTo(cx, s * 0.12);
    ctx.bezierCurveTo(s * 0.9, s * 0.55, s * 0.68, s * 0.9, cx, s * 0.9);
    ctx.bezierCurveTo(s * 0.32, s * 0.9, s * 0.1, s * 0.55, cx, s * 0.12);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = s * 0.05;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
  });
}

/** A yellow lightning bolt — electrical service / production. */
export function boltImage(color = "#f5c518"): BillboardImage {
  return icon(`bolt:${color}`, (ctx, s) => {
    ctx.beginPath();
    ctx.moveTo(s * 0.58, s * 0.08);
    ctx.lineTo(s * 0.28, s * 0.55);
    ctx.lineTo(s * 0.47, s * 0.55);
    ctx.lineTo(s * 0.4, s * 0.92);
    ctx.lineTo(s * 0.74, s * 0.42);
    ctx.lineTo(s * 0.53, s * 0.42);
    ctx.closePath();
    ctx.fillStyle = color;
    ctx.fill();
    ctx.lineWidth = s * 0.045;
    ctx.strokeStyle = "#5c4a00";
    ctx.stroke();
  });
}

/** A red warning badge with "!" — equipment with unconnected input ports. */
export function warnImage(): BillboardImage {
  return icon("warn", (ctx, s) => {
    ctx.beginPath();
    ctx.moveTo(s / 2, s * 0.1);
    ctx.lineTo(s * 0.92, s * 0.86);
    ctx.lineTo(s * 0.08, s * 0.86);
    ctx.closePath();
    ctx.fillStyle = "#e02424";
    ctx.fill();
    ctx.lineWidth = s * 0.05;
    ctx.strokeStyle = "#ffffff";
    ctx.stroke();
    centeredGlyph(ctx, s, "!", "#ffffff");
  });
}

const LABEL_LINE_PX = 64; // canvas pixels for the one text line

/** A line of text on a dark rounded plate: a label for an object. */
export function textLabelImage(text: string): BillboardImage {
  return {
    key: `text-label:${text}`,
    draw: () => {
      const h = LABEL_LINE_PX;
      const canvas = document.createElement("canvas");
      const ctx = canvas.getContext("2d")!;
      const font = `bold ${Math.round(h * 0.6)}px sans-serif`;
      ctx.font = font;
      canvas.width = Math.max(h, Math.ceil(ctx.measureText(text).width + h * 0.5));
      canvas.height = h;
      ctx.beginPath();
      ctx.roundRect(1, 1, canvas.width - 2, h - 2, h * 0.25);
      ctx.fillStyle = "#0f172a";
      ctx.fill();
      ctx.lineWidth = 3;
      ctx.strokeStyle = "#fbbf24";
      ctx.stroke();
      ctx.font = font; // sizing the canvas reset it
      ctx.fillStyle = "#fde68a";
      ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      ctx.fillText(text, canvas.width / 2, h / 2 + 2);
      return canvas;
    },
  };
}
