// A provider's SHARED worker budget: how many of its jobs all of its runs -- sweeps, apply-changes,
// collection requests -- may have in flight together (`/admin/asset-runs/max-parallel`). By default
// every live worker serving it (capped at what it declares; one for a single licensed seat); an
// admin may lower it. Pure helpers for `ProviderMaxParallel`, tested without a DOM.

import type {RunBudget} from "@/services/api/assetSchedules";

/** What the empty field means: "all live workers (N)", or why there is no number. */
export function defaultBudgetLabel(b: Pick<RunBudget, "live_workers" | "declared"> | null): string {
    if (!b || b.live_workers == null) return "all live workers";
    if (b.declared === 1) return "one at a time (the provider declares no parallel work)";
    const n = b.declared != null ? Math.min(b.declared, b.live_workers) : b.live_workers;
    return `all live workers (${n})`;
}

/** The field's text as a cap: blank is `null` (the default), a whole number >= 1 is itself; anything
 *  else is refused with the reason. */
export function parseMaxParallel(text: string): {value: number | null} | {error: string} {
    const t = text.trim();
    if (!t) return {value: null};
    if (!/^\d+$/.test(t) || Number(t) < 1) return {error: "a whole number of at least 1, or blank for all live workers"};
    return {value: Number(t)};
}

/** "2 of 5 in use" -- the line under the field. */
export function budgetUsage(b: RunBudget | null): string {
    if (!b || b.cap == null) return "";
    return `${b.in_use ?? 0} of ${b.cap} in use now`;
}
