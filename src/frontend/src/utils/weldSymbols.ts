// Weld symbols in the browser: each weld type's ISO 2553 basic symbol as a small SVG icon, drawn from
// the geometry `ada.api.weld_symbols` defines (generated into ./weldSymbols.generated.ts by
// scripts/codegen/gen_weld_symbols.py), with no round trip to the server. The SVG is the one Python's
// `WeldSymbol.to_svg` draws, character for character (src/__tests__/utils/weldSymbols.test.ts holds the
// two to it), so a symbol looks the same whether the server or the viewer drew it.

import { WELD_SYMBOLS, type WeldSymbolGeometry } from "./weldSymbols.generated";

export type { WeldSymbolGeometry } from "./weldSymbols.generated";

export interface WeldSymbolSvgOptions {
  /** Width and height of the square icon, px. Default 24. */
  size?: number;
  /** Colour of everything drawn; `currentColor` (the default) takes the text colour where the SVG is inlined. */
  stroke?: string;
  /** Line width in symbol units (the symbol's box is 1 wide). Default 0.07. */
  strokeWidth?: number;
  /** Draw the reference line the symbol stands on. Default true. */
  referenceLine?: boolean;
  /**
   * Which side of the reference line the weld is on, ISO 2553 system A: `"arrow"` (the default) above the
   * line, `"other"` below it (mirrored), `"both"` the two (a double fillet, a double V, ...). A symbol centred
   * on the line (spot, seam) is the same on any side.
   */
  side?: WeldSymbolSide;
}

export type WeldSymbolSide = "arrow" | "other" | "both";

const mirror = (paths: readonly (readonly number[])[]): number[][] => paths.map((p) => p.map((v, i) => (i % 2 ? -v : v)));

/** The symbol on `side` of the line, as Python's `WeldSymbol.on_side`. */
function onSide(symbol: WeldSymbolGeometry, side: WeldSymbolSide): WeldSymbolGeometry {
  if (side === "arrow" || symbol.centred) return symbol;
  const below = { ...symbol, strokes: mirror(symbol.strokes), outlines: mirror(symbol.outlines), fills: mirror(symbol.fills) };
  if (side === "other") return below;
  return {
    ...symbol,
    strokes: [...symbol.strokes, ...below.strokes],
    outlines: [...symbol.outlines, ...below.outlines],
    fills: [...symbol.fills, ...below.fills],
  };
}

/** Older names `ada.api.fasteners.WeldType.from_str` still takes. */
const LEGACY: Readonly<Record<string, string>> = { V: "BEVEL_GROOVE_SINGLE_V_BUTT" };

/** The `WeldType` value a weld type is called by: `FILLET`, `fillet` and `WELD_TYPE_FILLET` are one. */
function weldTypeKey(type: string): string {
  const key = type.toUpperCase().replace(/^WELD_TYPE_/, "");
  return LEGACY[key] ?? key;
}

/** A weld type's symbol as geometry; null for `NONE`, an unknown type, or none given. */
export function weldSymbol(type: string | null | undefined): WeldSymbolGeometry | null {
  if (!type) return null;
  const key = weldTypeKey(type);
  return Object.prototype.hasOwnProperty.call(WELD_SYMBOLS, key) ? WELD_SYMBOLS[key] : null;
}

/** As Python's `_n`: 3 decimals, no trailing zeros, never `-0`. */
function n(v: number): string {
  const s = v.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
  return s === "" || s === "-0" ? "0" : s;
}

/**
 * A weld type's symbol (ISO 2553, system A) as a square SVG icon on its reference line, as markup; null
 * for `NONE` or a type it does not know. `type` is an `ada.api.fasteners.WeldType` value, in any case,
 * with or without a `WELD_TYPE_` prefix. Inline it, or use it as an image's `data:` URL.
 */
export function weldSymbolSvg(type: string | null | undefined, options: WeldSymbolSvgOptions = {}): string | null {
  const found = weldSymbol(type);
  if (!found) return null;
  const { size = 24, stroke = "currentColor", strokeWidth = 0.07, referenceLine = true, side = "arrow" } = options;
  if (side !== "arrow" && side !== "other" && side !== "both") throw new Error(`weldSymbolSvg: side must be "arrow", "other" or "both", not ${JSON.stringify(side)}`);
  const symbol = onSide(found, side);
  const paths = [...symbol.strokes, ...symbol.outlines, ...symbol.fills];
  const xs: number[] = referenceLine ? [0, 1] : [];
  const ys: number[] = referenceLine ? [0] : [];
  for (const p of paths) {
    for (let i = 0; i < p.length; i += 2) {
      xs.push(p[i]);
      ys.push(p[i + 1]);
    }
  }
  const pad = 0.12;
  const x0 = Math.min(...xs) - pad;
  const x1 = Math.max(...xs) + pad;
  const y0 = Math.min(...ys) - pad;
  const y1 = Math.max(...ys) + pad;
  const span = Math.max(x1 - x0, y1 - y0);
  // Square, centred on the symbol; SVG's y runs down, so the symbol's y is negated.
  const cx = (x0 + x1) / 2;
  const cy = (y0 + y1) / 2;
  const view = `${n(cx - span / 2)} ${n(-cy - span / 2)} ${n(span)} ${n(span)}`;
  const pts = (p: readonly number[]): string => {
    const out: string[] = [];
    for (let i = 0; i < p.length; i += 2) out.push(`${n(p[i])},${n(-p[i + 1])}`);
    return out.join(" ");
  };
  const common = `stroke="${stroke}" stroke-width="${n(strokeWidth)}" stroke-linecap="round" stroke-linejoin="round"`;
  const parts: string[] = [];
  if (referenceLine) parts.push(`<line x1="0" y1="0" x2="1" y2="0" ${common}/>`);
  for (const p of symbol.strokes) parts.push(`<polyline points="${pts(p)}" fill="none" ${common}/>`);
  for (const p of symbol.outlines) parts.push(`<polygon points="${pts(p)}" fill="none" ${common}/>`);
  for (const p of symbol.fills) parts.push(`<polygon points="${pts(p)}" fill="${stroke}" ${common}/>`);
  return (
    `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="${view}">` +
    `<title>${symbol.description}</title>${parts.join("")}</svg>`
  );
}
