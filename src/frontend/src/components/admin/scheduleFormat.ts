// Timestamp formatting shared by the two schedule panels (audit sweeps and
// plugin jobs).
//
// Shared rather than copied because the pair is read together: the panels sit
// on one page, and "next: in 34m" meaning one thing above and another below is
// the kind of drift nobody notices until they are comparing two schedules that
// disagree about when they will fire.

/** Absolute local time, or an em dash when there is nothing to show.
 *
 * Falls back to the raw string on an unparseable value rather than rendering
 * "Invalid Date": the server's value is at least a clue about what went wrong. */
export function fmtTimestamp(iso: string | null): string {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString();
}

/** "in 34m" / "3h ago" — the half an operator actually reads, next to the
 * absolute time for the half they need when comparing against a log. */
export function fmtRelative(iso: string | null): string {
    if (!iso) return "";
    const ms = new Date(iso).getTime() - Date.now();
    const sign = ms >= 0 ? "in" : "ago";
    const abs = Math.abs(ms);
    if (abs < 60_000) return `${sign === "in" ? "in <1m" : "<1m ago"}`;
    if (abs < 3600_000) return `${sign} ${Math.round(abs / 60_000)}m`;
    if (abs < 86400_000) return `${sign} ${Math.round(abs / 3600_000)}h`;
    return `${sign} ${Math.round(abs / 86400_000)}d`;
}

/** Common 5-field cron patterns, offered instead of hand-typing.
 *
 * Free-text entry stays available — these are shortcuts for the cases that
 * account for nearly all real usage, and the server validates either way. */
export const CRON_PRESETS: {label: string; expr: string}[] = [
    {label: "Every hour", expr: "0 * * * *"},
    {label: "Every 4 hours", expr: "0 */4 * * *"},
    {label: "Daily 02:00 UTC", expr: "0 2 * * *"},
    {label: "Weekly (Mon 02:00)", expr: "0 2 * * 1"},
    {label: "Weekdays 02:00", expr: "0 2 * * 1-5"},
];
