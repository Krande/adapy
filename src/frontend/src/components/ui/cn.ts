/**
 * Join class names, dropping falsy entries.
 *
 * Deliberately ~10 lines rather than a `clsx`/`classnames` dependency: the desktop
 * build inlines everything into one HTML file, so every dependency is paid for in
 * full, and this is the whole of what the primitives need.
 */
export type ClassValue = string | false | null | undefined;

export function cn(...parts: ClassValue[]): string {
    let out = "";
    for (const p of parts) {
        if (!p) continue;
        out = out ? `${out} ${p}` : p;
    }
    return out;
}
