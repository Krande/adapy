// Human-readable numbers for the admin and storage panels: byte counts,
// durations, and "how long ago". One module because a dozen panels each grew
// their own copy of the same three ladders, and the copies had drifted in the
// small ways that make two tables on one page disagree (KB vs KiB, "—" vs "–"
// for a missing value, whether 0 B is a size or an absence).
//
// The options exist to preserve exactly what each panel rendered before the
// copies were folded together — they are not a style guide. A new call site
// should take the defaults.

export interface FormatBytesOptions {
    /** Rendered for null/undefined (and for 0 when `emptyOnZero`). Default "—". */
    empty?: string;
    /** Unit labels. Both ladders divide by 1024; "iec" only changes the label. Default "si". */
    units?: "si" | "iec";
    /** Treat 0 as "nothing to show" rather than as a size. */
    emptyOnZero?: boolean;
    /** Stop the ladder at MB: anything larger renders as a large MB count. */
    maxUnit?: "MB" | "GB";
    /** Single ladder to TB with 0 decimals at >= 10 and 1 below ("15 MB", "1.5 GB"). */
    compact?: boolean;
}

const KIB = 1024;
const MIB = 1024 * 1024;
const GIB = 1024 * 1024 * 1024;

/** "512 B", "1.5 KB", "12.3 MB", "1.25 GB". */
export function formatBytes(n: number | null | undefined, opts: FormatBytesOptions = {}): string {
    const empty = opts.empty ?? "—";
    if (n == null) return empty;
    if (opts.emptyOnZero && !n) return empty;
    if (opts.compact) return formatBytesCompact(n);
    const [kb, mb, gb] = opts.units === "iec" ? ["KiB", "MiB", "GiB"] : ["KB", "MB", "GB"];
    if (n < KIB) return `${n} B`;
    if (n < MIB) return `${(n / 1024).toFixed(1)} ${kb}`;
    if (opts.maxUnit === "MB") return `${(n / MIB).toFixed(1)} ${mb}`;
    if (n < GIB) return `${(n / 1024 / 1024).toFixed(1)} ${mb}`;
    return `${(n / 1024 / 1024 / 1024).toFixed(2)} ${gb}`;
}

function formatBytesCompact(n: number): string {
    if (n < KIB) return `${n} B`;
    const u = ["KB", "MB", "GB", "TB"];
    let v = n / 1024;
    let i = 0;
    while (v >= 1024 && i < u.length - 1) {
        v /= 1024;
        i++;
    }
    return `${v.toFixed(v >= 10 ? 0 : 1)} ${u[i]}`;
}

export interface FormatDurationOptions {
    /** Rendered for a negative input; without it the sign is kept ("-5s"). */
    negative?: string;
}

/** Coarse elapsed time in one unit: "42s", "3m", "2h", "5d". Input is seconds. */
export function formatDuration(seconds: number, opts: FormatDurationOptions = {}): string {
    if (opts.negative !== undefined && seconds < 0) return opts.negative;
    if (seconds < 60) return `${Math.round(seconds)}s`;
    if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
    if (seconds < 86400) return `${Math.round(seconds / 3600)}h`;
    return `${Math.round(seconds / 86400)}d`;
}

export interface FormatMillisOptions {
    /** Rendered for null/undefined. Default "—". */
    empty?: string;
    /** "12.34 s" and "2m 3.4s" instead of "12.3 s" for everything over a second. */
    precise?: boolean;
}

/** Sub-second in ms, otherwise seconds: "123 ms", "1.2 s". Input is milliseconds. */
export function formatMillis(ms: number | null | undefined, opts: FormatMillisOptions = {}): string {
    if (ms == null) return opts.empty ?? "—";
    if (ms < 1000) return `${ms} ms`;
    if (!opts.precise) return `${(ms / 1000).toFixed(1)} s`;
    const s = ms / 1000;
    if (s < 60) return `${s.toFixed(2)} s`;
    const m = Math.floor(s / 60);
    const rem = (s - m * 60).toFixed(1);
    return `${m}m ${rem}s`;
}

/** "42s ago", "3m ago", "2h ago", "5d ago" — or "in the future". Both inputs are epoch seconds. */
export function formatRelativeTime(epochSeconds: number, nowEpochSeconds: number): string {
    const dt = nowEpochSeconds - epochSeconds;
    if (dt < 0) return "in the future";
    if (dt < 60) return `${Math.round(dt)}s ago`;
    if (dt < 3600) return `${Math.round(dt / 60)}m ago`;
    if (dt < 86400) return `${Math.round(dt / 3600)}h ago`;
    return `${Math.round(dt / 86400)}d ago`;
}
