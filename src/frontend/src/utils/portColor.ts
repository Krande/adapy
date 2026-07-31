import type { CatalogPort, PortCategory } from "@/services/viewerApi";

// Single source of truth for port/nozzle colours, shared by the equipment
// preview (arrow glyphs), the catalog port editor (swatch + accent bar) and any
// viewer overlay that draws port positions/vectors. A port may carry an explicit
// ``color`` override; when it doesn't, the colour is derived from its category so
// process/electrical/signal stay visually consistent by default.

export const CATEGORY_COLOR_HEX: Record<PortCategory, string> = {
    process: "#38bdf8", // cyan
    electrical: "#f59e0b", // amber
    signal: "#ec4899", // pink
};

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

/** The colour a port should render as: its explicit override when valid,
 * otherwise the category default. Always a ``#rrggbb`` string. */
export function portColorHex(port: Pick<CatalogPort, "category" | "color">): string {
    return normalizeHex(port.color) ?? CATEGORY_COLOR_HEX[port.category];
}

/** ``#rrggbb`` → a THREE-friendly 0xRRGGBB integer. */
export function hexToInt(hex: string): number {
    const norm = normalizeHex(hex) ?? "#000000";
    return parseInt(norm.slice(1), 16);
}

/** Convenience: a port's colour as a THREE 0xRRGGBB integer. */
export function portColorInt(port: Pick<CatalogPort, "category" | "color">): number {
    return hexToInt(portColorHex(port));
}
