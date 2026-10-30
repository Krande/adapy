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
    /** What core said: `applied`, `refused` (no baseline), `failed`, `skipped`. */
    status: string;
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
    if (typeof v === "string") {
        const ok = /^(applied|ok|done)$/i.test(v);
        return {role, applied: ok, status: ok ? "applied" : v, warning: ok ? null : v};
    }
    if (typeof v === "boolean") return {role, applied: v, status: v ? "applied" : "refused", warning: null};
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
    return {role, applied, status: status ?? (applied ? "applied" : "refused"), warning};
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
        // Core puts the publish outcome per role BESIDE the block, on the item (`roles`).
        roles: readRoles(block.roles ?? item.roles ?? block.publish ?? block.outcomes),
    };
}

/** The item status of a collection an apply-changes run left out: the provider store holds no full
 *  export of it, so it was not asked for changes and got no cursor. */
export const NO_FULL_EXPORT = "no_full_export";

export interface TargetOutcome {
    target: string;
    status: string;
    revision: string | null;
    /** The error of a failed target, the reason a skipped one did not run, or the provider's note. */
    note: string | null;
    totalMs: number | null;
}

/** A multi-target sweep item's outcome per target (`targets`: one provider job per ticked
 *  `sweep_target`, in order), or [] for an item that ran one job. */
export function sweepTargetsOf(item: unknown): TargetOutcome[] {
    if (!isRecord(item)) return [];
    const timings = isRecord(item.timings) ? item.timings : {};
    const raw = isRecord(item.targets) ? item.targets : isRecord(timings.targets) ? timings.targets : null;
    if (!raw) return [];
    const out: TargetOutcome[] = [];
    for (const [target, v] of Object.entries(raw)) {
        if (!isRecord(v)) continue;
        const t = isRecord(v.timings) ? v.timings : {};
        const parts = ["queued_ms", "fetch_ms", "publish_ms"].map((k) => Number(t[k])).filter((n) => Number.isFinite(n));
        out.push({
            target,
            status: text(v.status) ?? "unknown",
            revision: text(v.revision),
            note: text(v.error) ?? text(v.message),
            totalMs: parts.length ? parts.reduce((a, b) => a + b, 0) : null,
        });
    }
    return out;
}

/** A run row's progress line from its item counts: "finished/to run · n failed · ...". A collection
 *  left out for want of a full export is finished (nothing more will happen to it this run). */
export function sweepCountsText(run: {total: number; counts?: Partial<Record<string, number>>}): string {
    const c = run.counts ?? {};
    const finished =
        (c.done ?? 0) + (c.unchanged ?? 0) + (c.nothing ?? 0) + (c.error ?? 0) + (c.skipped ?? 0) + (c[NO_FULL_EXPORT] ?? 0);
    // Out of the collections that will run: ticked-off ones are not "left to do".
    const parts = [`${finished}/${run.total - (c.excluded ?? 0)}`];
    if (c.error) parts.push(`${c.error} failed`);
    if (c.excluded) parts.push(`${c.excluded} not run`);
    if (c.unchanged) parts.push(`${c.unchanged} unchanged`);
    if (c[NO_FULL_EXPORT]) parts.push(`${c[NO_FULL_EXPORT]} without a full export`);
    return parts.join(" · ");
}

/** A run's run-level warnings (an apply-changes run names every collection it left out). */
export function runWarningsOf(run: unknown): string[] {
    if (!isRecord(run) || !Array.isArray(run.warnings)) return [];
    return run.warnings.map(text).filter((w): w is string => !!w);
}

export interface FullExportLink {
    runId: string;
    collections: string[];
}

/** The full project export an apply-changes run queued for what it left out, or null. */
export function queuedFullExportOf(run: unknown): FullExportLink | null {
    const q = isRecord(run) && isRecord(run.summary) ? run.summary.queued_full_exports : null;
    if (!isRecord(q)) return null;
    const runId = text(q.run_id);
    if (!runId) return null;
    const collections = Array.isArray(q.collections) ? q.collections.map(text).filter((c): c is string => !!c) : [];
    return {runId, collections};
}

/** On a queued full export: the apply-changes run that queued it, or null. */
export function queuedByApplyRunOf(run: unknown): string | null {
    return isRecord(run) && isRecord(run.summary) ? text(run.summary.apply_changes_run_id) : null;
}

/** "3 changed · 1 inserted · 2 deleted", leaving out the zeros. */
export function countsLine(c: ApplyCounts | null): string {
    if (!c) return "";
    const parts = COUNT_KEYS.filter((k) => c[k] > 0).map((k) => `${c[k]} ${k}`);
    return parts.length ? parts.join(" · ") : "0 changed";
}
