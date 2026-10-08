/** Whether a CSS colour (#rgb, #rrggbb, rgb()/rgba()) is light: relative luminance > 0.5. */
export function isLightColor(color: string): boolean {
    let r = 0, g = 0, b = 0;
    const hex = color.trim().match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (hex) {
        const h = hex[1].length === 3 ? hex[1].replace(/./g, (c) => c + c) : hex[1];
        r = parseInt(h.slice(0, 2), 16); g = parseInt(h.slice(2, 4), 16); b = parseInt(h.slice(4, 6), 16);
    } else {
        const m = color.match(/rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)/i);
        if (!m) return false;
        r = +m[1]; g = +m[2]; b = +m[3];
    }
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255 > 0.5;
}
