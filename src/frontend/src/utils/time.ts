// Timestamps on the wire are UTC with an explicit offset, and every comparison
// or delta is computed on that. Display is the only place a timezone shift
// belongs, and it belongs to the viewer: these render in whatever zone the
// browser is in, so the same row reads correctly for everyone looking at it.
//
// The trap these exist to close is `new Date(t).toISOString().slice(0, 10)`,
// which looks local but is not — it re-serialises to UTC, so a file touched at
// 01:30 in a UTC+2 zone displays as the previous day. Date-only renderings hide
// the shift instead of making it obvious, which is why it survived.
//
// `sv-SE` is a deliberate locale pick, not a language: it is the one whose
// conventional format is ISO-shaped (YYYY-MM-DD HH:MM:SS), so output stays
// sortable and unambiguous while the VALUES move to local time.

const ISO_LIKE = "sv-SE";

/** Parse a wire timestamp; null when absent or unparseable. */
function parse(value: string | number | Date | null | undefined): Date | null {
    if (value === null || value === undefined || value === "") return null;
    const d = value instanceof Date ? value : new Date(value);
    return Number.isNaN(d.getTime()) ? null : d;
}

/** `YYYY-MM-DD` in the viewer's timezone. */
export function localDate(value: string | number | Date | null | undefined, fallback = ""): string {
    const d = parse(value);
    return d ? d.toLocaleDateString(ISO_LIKE) : fallback;
}

/** `YYYY-MM-DD HH:MM:SS` in the viewer's timezone. */
export function localDateTime(value: string | number | Date | null | undefined, fallback = ""): string {
    const d = parse(value);
    return d ? d.toLocaleString(ISO_LIKE) : fallback;
}
