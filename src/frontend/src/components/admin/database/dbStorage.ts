import type {DbAreaStorage, DbCollectionRef, DbPruneSpec} from "@/services/api/adminDb";

// Admin -> Database, the rules worth testing: reading the prune forms into the request the API
// takes, and diffing the retention fields into what changed. React-free on purpose (see
// adminTabs.ts for why that matters to the test runner).

/** A prune form as typed: days as text, and for the attribute index the collections ticked. */
export interface PruneForm {
    days: string;
    /** `collectionKey`s; attribute index only. */
    collections: ReadonlySet<string>;
}

export const MIN_PRUNE_DAYS = 1;

/** A whole number of days >= 1, `null` for an empty field, `"invalid"` otherwise. */
export function parseDays(text: string): number | null | "invalid" {
    const t = text.trim();
    if (!t) return null;
    if (!/^\d+$/.test(t)) return "invalid";
    const n = Number(t);
    return n >= MIN_PRUNE_DAYS ? n : "invalid";
}

/** One attribute-index collection as a string key (a Set member, a checkbox id). */
export function collectionKey(ref: DbCollectionRef): string {
    return JSON.stringify([ref.scope, ref.provider, ref.collection]);
}

export function refOfKey(key: string): DbCollectionRef {
    const [scope, provider, collection] = JSON.parse(key) as [string, string, string];
    return {scope, provider, collection};
}

/** The prune request for ONE area's form, or an error to show beside it. Ticked collections win
 *  over a day count for the attribute index: the list is the more deliberate choice. */
export function pruneSpecFor(
    area: Pick<DbAreaStorage, "id" | "prune">,
    form: PruneForm,
): {spec: DbPruneSpec} | {error: string} {
    if (!area.prune) return {error: "this area is not pruned here"};
    if (area.prune === "collections" && form.collections.size) {
        return {spec: {collections: [...form.collections].map(refOfKey)}};
    }
    const days = parseDays(form.days);
    if (days === "invalid") return {error: `days must be a whole number of at least ${MIN_PRUNE_DAYS}`};
    if (days === null) {
        return {error: area.prune === "collections" ? "tick collections or give an age in days" : "give an age in days"};
    }
    return {spec: {older_than_days: days}};
}

/** The retention fields that differ from what is saved: a number to set, `null` to clear. A
 *  field left unparseable is reported, not sent. */
export function retentionChanges(
    saved: Readonly<Record<string, number>>,
    edited: Readonly<Record<string, string>>,
): {changes: Record<string, number | null>; invalid: string[]} {
    const changes: Record<string, number | null> = {};
    const invalid: string[] = [];
    for (const [area, text] of Object.entries(edited)) {
        const days = parseDays(text);
        if (days === "invalid") {
            invalid.push(area);
            continue;
        }
        if ((saved[area] ?? null) !== days) changes[area] = days;
    }
    return {changes, invalid};
}

/** A scope a partition can be shared into, as the "Share with…" control lists it. */
export interface ShareTarget {
    /** Storage prefix: `shared`, `users/<sub>`, `projects/<id>`. */
    scope: string;
    label: string;
}

/** What "Share with…" offers: Shared, the admin's own personal scope, every live project -- never
 *  the partition's own scope (it is searchable there already). Another user's personal scope is
 *  not offered: there is no list of them to pick from, and a share there reads only for its owner. */
export function shareTargets(
    meSub: string | null | undefined,
    projects: readonly {id: string; name: string; archived_at?: string | null}[],
    own: string | null,
): ShareTarget[] {
    const out: ShareTarget[] = [{scope: "shared", label: "Shared"}];
    if (meSub) out.push({scope: `users/${meSub}`, label: "My files"});
    for (const p of [...projects].filter((p) => !p.archived_at).sort((a, b) => a.name.localeCompare(b.name))) {
        out.push({scope: `projects/${p.id}`, label: p.name});
    }
    return out.filter((t) => t.scope !== own);
}

/** A scope prefix by the name `targets` give it, else the prefix itself. */
export function shareLabel(scope: string, targets: readonly ShareTarget[]): string {
    return targets.find((t) => t.scope === scope)?.label ?? scope;
}

/** "1.2M" / "~1.2M": an estimate is marked as one. */
export function formatRows(n: number, exact: boolean): string {
    const abs =
        n >= 1e9 ? `${(n / 1e9).toFixed(1)}G` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${(n / 1e3).toFixed(0)}k` : String(n);
    return exact ? abs : `~${abs}`;
}

/** A share of the whole as a percentage with one decimal ("0.0" for none). */
export function sharePercent(part: number, whole: number): string {
    return whole > 0 ? ((part / whole) * 100).toFixed(1) : "0.0";
}

/** "2026-09-13 → 2026-10-12", one end if they are the same day, "—" when unknown. */
export function ageRange(oldest: string | null, newest: string | null): string {
    const day = (iso: string | null) => (iso ? iso.slice(0, 10) : null);
    const a = day(oldest);
    const b = day(newest);
    if (!a && !b) return "—";
    if (!a || !b || a === b) return (a ?? b) as string;
    return `${a} → ${b}`;
}
