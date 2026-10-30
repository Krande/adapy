// What an apply-changes run reports per collection (`ada.assets/apply-changes@1`, the item's
// `apply_changes` block): the element counts, whether the run was a baseline or found nothing, the
// cursor it moved from and to, and -- once core has written the patches -- how each staging role fared.
//
// Read DEFENSIVELY: the block rides on the sweep item and any field may be absent (an older core, a
// run that failed before the provider answered), so every part is optional and a malformed one is
// dropped rather than thrown on.

export interface ApplyCounts {
    changed: number;
    inserted: number;
    deleted: number;
    unplaceable: number;
}

export interface RoleOutcome {
    role: string;
    applied: boolean;
    /** Why a role was refused (e.g. no baseline), or a note on one that was applied. */
    warning: string | null;
}

export interface ApplyChangesView {
    counts: ApplyCounts | null;
    baseline: boolean;
    nothing: boolean;
    since: string | null;
    nextCursor: string | null;
    roles: RoleOutcome[];
}

const COUNT_KEYS: (keyof ApplyCounts)[] = ["changed", "inserted", "deleted", "unplaceable"];

function isRecord(v: unknown): v is Record<string, unknown> {
    return typeof v === "object" && v !== null && !Array.isArray(v);
}

const text = (v: unknown): string | null => (typeof v === "string" && v ? v : v == null || typeof v === "object" ? null : String(v));

function readCounts(v: unknown): ApplyCounts | null {
    if (!isRecord(v)) return null;
    const out = {changed: 0, inserted: 0, deleted: 0, unplaceable: 0};
    let any = false;
    for (const k of COUNT_KEYS) {
        const n = Number(v[k]);
        if (v[k] != null && Number.isFinite(n)) {
            out[k] = n;
            any = true;
        }
    }
    return any ? out : null;
}

/** One role's entry: a bare status word, or an object with `status` / `applied` / `refused` and a `warning`. */
function readRole(role: string, v: unknown): RoleOutcome | null {
    if (typeof v === "string") return {role, applied: /^(applied|ok|done)$/i.test(v), warning: /^(applied|ok|done)$/i.test(v) ? null : v};
    if (typeof v === "boolean") return {role, applied: v, warning: null};
    if (!isRecord(v)) return null;
    const status = text(v.status);
    const applied =
        typeof v.applied === "boolean"
            ? v.applied
            : typeof v.refused === "boolean"
              ? !v.refused
              : status != null
                ? /^(applied|ok|done)$/i.test(status)
                : !text(v.warning ?? v.error);
    const warning = text(v.warning) ?? text(v.error) ?? text(v.reason) ?? (!applied && status && !/^refused$/i.test(status) ? status : null);
    return {role, applied, warning};
}

function readRoles(v: unknown): RoleOutcome[] {
    const out: RoleOutcome[] = [];
    if (Array.isArray(v)) {
        for (const e of v) {
            const role = isRecord(e) ? text(e.role ?? e.name) : null;
            const r = role ? readRole(role, e) : null;
            if (r) out.push(r);
        }
    } else if (isRecord(v)) {
        for (const [role, e] of Object.entries(v)) {
            const r = readRole(role, e);
            if (r) out.push(r);
        }
    }
    return out;
}

/** The apply-changes block of a sweep item, or null when the item carries none (a sweep's). */
export function applyChangesOf(item: unknown): ApplyChangesView | null {
    if (!isRecord(item)) return null;
    const block = item.apply_changes ?? item.apply;
    if (!isRecord(block)) return null;
    return {
        counts: readCounts(block.counts),
        baseline: block.baseline === true,
        nothing: block.nothing === true,
        since: text(block.since),
        nextCursor: text(block.next_cursor),
        roles: readRoles(block.roles ?? block.publish ?? block.outcomes),
    };
}

/** "3 changed · 1 inserted · 2 deleted", leaving out the zeros. */
export function countsLine(c: ApplyCounts | null): string {
    if (!c) return "";
    const parts = COUNT_KEYS.filter((k) => c[k] > 0).map((k) => `${c[k]} ${k}`);
    return parts.length ? parts.join(" · ") : "0 changed";
}
