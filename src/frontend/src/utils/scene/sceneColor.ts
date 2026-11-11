// A colour as a caller names it (sRGB, as CSS: "#ffaa00", or [r, g, b] in 0..1) as the linear RGB
// a viewport draws with. No three.js in this module.

import type {SceneColor} from "@/plugins/registry";

/** One sRGB channel (0..1) as linear light. */
export function linearChannel(c: number): number {
    return c < 0.04045 ? c * 0.0773993808 : Math.pow(c * 0.9478672986 + 0.0521327014, 2.4);
}

// A CSS colour is parsed once: painting 20,000 elements names a handful of colours.
const parsed = new Map<string, readonly [number, number, number]>();
const MAX_PARSED = 4096;
let cssContext: CanvasRenderingContext2D | null | undefined;

/** A CSS colour as sRGB channels in 0..1: hex and rgb() here, anything else as the browser reads it. */
function cssChannels(css: string): [number, number, number] {
    const text = css.trim();
    const hex = /^#([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(text)?.[1];
    if (hex) {
        const wide = hex.length === 3 ? hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2] : hex;
        const n = parseInt(wide, 16);
        return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
    }
    const rgb = /^rgba?\(\s*(\d+)\s*[, ]\s*(\d+)\s*[, ]\s*(\d+)/i.exec(text);
    if (rgb) return [Math.min(255, +rgb[1]) / 255, Math.min(255, +rgb[2]) / 255, Math.min(255, +rgb[3]) / 255];
    if (cssContext === undefined) cssContext = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d");
    if (cssContext) {
        // The browser gives back "#rrggbb" (or rgba() for a see-through colour) for whatever it understood.
        cssContext.fillStyle = "#ffffff";
        cssContext.fillStyle = text;
        const read = String(cssContext.fillStyle);
        if (read !== text) return cssChannels(read);
    }
    console.warn(`[plugins] unknown colour "${css}"`);
    return [1, 1, 1];
}

/** `c` as linear RGB into `out[at .. at + 2]`. */
export function linearColorInto(c: SceneColor, out: Float32Array | number[], at = 0): void {
    if (typeof c !== "string") {
        out[at] = linearChannel(c[0]);
        out[at + 1] = linearChannel(c[1]);
        out[at + 2] = linearChannel(c[2]);
        return;
    }
    let rgb = parsed.get(c);
    if (!rgb) {
        const [r, g, b] = cssChannels(c);
        rgb = [linearChannel(r), linearChannel(g), linearChannel(b)];
        if (parsed.size >= MAX_PARSED) parsed.clear();
        parsed.set(c, rgb);
    }
    out[at] = rgb[0];
    out[at + 1] = rgb[1];
    out[at + 2] = rgb[2];
}

/** An sRGB hex number (0xrrggbb) as linear RGB. */
export function linearHex(hex: number): [number, number, number] {
    return [linearChannel(((hex >> 16) & 255) / 255), linearChannel(((hex >> 8) & 255) / 255), linearChannel((hex & 255) / 255)];
}

/** "#rrggbb" as linear RGB; null when the text is not that. */
export function linearHexText(text: string): [number, number, number] | null {
    return /^#[0-9a-f]{6}$/i.test(text) ? linearHex(parseInt(text.slice(1), 16)) : null;
}

/** `c` as linear RGB. */
export function linearColor(c: SceneColor): [number, number, number] {
    const out: [number, number, number] = [0, 0, 0];
    linearColorInto(c, out);
    return out;
}
