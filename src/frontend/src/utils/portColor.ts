import type { CatalogPort, PortCategory } from "@/services/viewerApi";

// Single source of truth for port/nozzle colours, shared by the equipment
// preview (arrow glyphs), the catalog port editor (swatch + accent bar) and any
// viewer overlay that draws port positions/vectors. A port may carry an explicit
// ``color`` override; when it doesn't, the colour is derived *per port* from its
// name so every port on an equipment gets a visually distinct colour (the old
// per-category defaults made same-category ports indistinguishable). The
// category still seeds a hue family so the auto colour stays loosely
// recognisable (process = cyans, electrical = ambers, signal = pinks).

export const CATEGORY_COLOR_HEX: Record<PortCategory, string> = {
    process: "#38bdf8", // cyan
    electrical: "#f59e0b", // amber
    signal: "#ec4899", // pink
};

// Base hue (degrees) per category; the per-port hash spreads ports around it.
const CATEGORY_HUE: Record<PortCategory, number> = {
    process: 199, // cyan family
    electrical: 38, // amber family
    signal: 330, // pink family
};

/** Deterministic 32-bit-ish string hash (FNV-1a style), stable across runs. */
function hashString(seed: string): number {
    let h = 2166136261;
    for (let i = 0; i < seed.length; i++) {
        h ^= seed.charCodeAt(i);
        h = Math.imul(h, 16777619);
    }
    return h >>> 0;
}

function hslToHex(h: number, s: number, l: number): string {
    const c = (1 - Math.abs(2 * l - 1)) * s;
    const hp = ((h % 360) + 360) % 360 / 60;
    const x = c * (1 - Math.abs((hp % 2) - 1));
    let r = 0, g = 0, b = 0;
    if (hp < 1) [r, g, b] = [c, x, 0];
    else if (hp < 2) [r, g, b] = [x, c, 0];
    else if (hp < 3) [r, g, b] = [0, c, x];
    else if (hp < 4) [r, g, b] = [0, x, c];
    else if (hp < 5) [r, g, b] = [x, 0, c];
    else [r, g, b] = [c, 0, x];
    const m = l - c / 2;
    const to = (v: number) => Math.round((v + m) * 255).toString(16).padStart(2, "0");
    return "#" + to(r) + to(g) + to(b);
}

/** A distinct, deterministic colour for a port from its name (and category as a
 * hue anchor). Two differently-named ports get different colours; the same port
 * always gets the same colour. */
export function uniquePortColorHex(name: string, category: PortCategory): string {
    const hue = (CATEGORY_HUE[category] + (hashString(name) % 300) - 150 + 360) % 360;
    return hslToHex(hue, 0.68, 0.58);
}

// Golden-angle hue rotation: consecutive indices land ~137.5° apart, so the
// first several ports on an equipment get maximally-separated, guaranteed-unique
// hues — no two I/O share a colour, even across categories (the per-name hash
// could otherwise collide, e.g. a process "suction" landing on the same hue as
// an electrical "power"). Used whenever the caller knows a port's position in
// the equipment's port list; name-hashing stays the fallback.
const GOLDEN_ANGLE = 137.508;
const HUE_START = 205; // begin in the cyan/process family for familiarity

/** A guaranteed-distinct colour for the port at ``index`` in an equipment's
 * port list (indices 0,1,2,… map to well-separated hues). */
export function uniquePortColorHexByIndex(index: number): string {
    const hue = (HUE_START + index * GOLDEN_ANGLE) % 360;
    return hslToHex(hue, 0.68, 0.58);
}

/** Normalise ``#rgb``/``#rrggbb`` (any case) to lowercase ``#rrggbb``; returns
 * null for anything that isn't a hex colour. */
export function normalizeHex(value: string | null | undefined): string | null {
    if (!value) return null;
    const s = value.trim().toLowerCase();
    if (/^#[0-9a-f]{6}$/.test(s)) return s;
    if (/^#[0-9a-f]{3}$/.test(s)) {
        return "#" + s[1] + s[1] + s[2] + s[2] + s[3] + s[3];
    }
    return null;
}

/** The colour a port should render as: its explicit override when valid; else,
 * when its position (``index``) in the equipment's port list is known, a
 * guaranteed-distinct golden-angle colour; else a per-name colour (falling back
 * to the category colour when no name is available). Always a ``#rrggbb`` string.
 * Pass ``index`` wherever the full port list is iterated so every I/O is unique. */
export function portColorHex(
    port: Pick<CatalogPort, "name" | "category" | "color">,
    index?: number,
): string {
    const override = normalizeHex(port.color);
    if (override) return override;
    if (index != null) return uniquePortColorHexByIndex(index);
    if (port.name) return uniquePortColorHex(port.name, port.category);
    return CATEGORY_COLOR_HEX[port.category];
}

/** ``#rrggbb`` → a THREE-friendly 0xRRGGBB integer. */
export function hexToInt(hex: string): number {
    const norm = normalizeHex(hex) ?? "#000000";
    return parseInt(norm.slice(1), 16);
}

/** Convenience: a port's colour as a THREE 0xRRGGBB integer. */
export function portColorInt(
    port: Pick<CatalogPort, "name" | "category" | "color">,
    index?: number,
): number {
    return hexToInt(portColorHex(port, index));
}
