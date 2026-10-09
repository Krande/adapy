// The step picker's list: the cases the bake stored, then the load
// combinations it left to compute on request.
//
// A bake_version 4 manifest keeps ``fields[].steps`` to the STORED cases (an
// older viewer reads it unchanged) and lists the rest under
// ``combination_steps``. The viewer offers both in one list of slots. Stored
// steps keep their own index as their slot -- slot i IS stored step i -- and
// the combinations follow in case-number order, so every place that indexes
// a field's steps by the store's ``stepIndex`` stays right for stored cases,
// and a slot past the field's own steps is a combination (``slotRef``).
//
// Pure: no store, no fetch. The picker, the loader, the legend and the
// animation all derive what they need from here.

import type {FeaStepRef} from "@/services/fea/feaStepRef";
import type {CaseStatusInfo} from "@/services/fea/feaCaseResolver";
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";

export interface CaseStepSlot {
    /** Position in the merged list; equals the stored index for stored steps. */
    slot: number;
    ref: FeaStepRef;
    kind: "stored" | "combination";
    /** The case number (a stored step's value). */
    value: number;
    /** What the picker shows as the step's own label. */
    label: string;
    name?: string;
    /** A combination's recipe, e.g. ``1.2·dead + 1.1·live``. */
    makeup?: string;
    needsRaw?: boolean;
    recipeHash?: string;
}

/** Whether the manifest offers combinations to compute on request. */
export function hasLazyCases(manifest: Pick<FeaManifest, "combination_steps"> | null | undefined): boolean {
    return !!manifest?.combination_steps?.length;
}

/** Whether ``field`` takes combination slots at all: a model property does not
 *  vary by case, so its single step is all there is. */
function fieldTakesCases(field: FeaManifestField | null | undefined): boolean {
    return !field || field.category !== "property";
}

/** The merged list for ``field`` (or for the manifest's stored steps when no
 *  field is given). A manifest without ``combination_steps`` (bake_version 3)
 *  gives exactly the field's steps, as before. */
export function mergeCaseSteps(
    manifest: Pick<FeaManifest, "combination_steps" | "result_cases" | "baked_steps"> | null | undefined,
    field?: FeaManifestField | null,
): CaseStepSlot[] {
    const out: CaseStepSlot[] = [];
    if (field) {
        for (const s of field.steps) {
            out.push({
                slot: out.length,
                ref: {stored: s.i},
                kind: "stored",
                value: s.value,
                label: s.label,
                ...(s.name ? {name: s.name} : {}),
            });
        }
    } else {
        const names = new Map((manifest?.result_cases ?? []).map((c) => [c.n, c.name]));
        for (const [i, v] of (manifest?.baked_steps ?? []).entries()) {
            const name = names.get(v);
            out.push({slot: out.length, ref: {stored: i}, kind: "stored", value: v, label: String(v), ...(name ? {name} : {})});
        }
    }
    if (!fieldTakesCases(field)) return out;
    const makeups = new Map((manifest?.result_cases ?? []).map((c) => [c.n, c.makeup]));
    const combos = [...(manifest?.combination_steps ?? [])].sort((a, b) => a.n - b.n);
    for (const c of combos) {
        const makeup = c.makeup ?? makeups.get(c.n);
        out.push({
            slot: out.length,
            ref: {case: c.n},
            kind: "combination",
            value: c.n,
            label: String(c.n),
            ...(c.name ? {name: c.name} : {}),
            ...(makeup ? {makeup} : {}),
            needsRaw: !!c.needs_raw,
            recipeHash: c.recipe_hash,
        });
    }
    return out;
}

/** How many slots ``field`` offers: its own steps plus the combinations. */
export function slotCount(
    manifest: Pick<FeaManifest, "combination_steps"> | null | undefined,
    field: FeaManifestField | null | undefined,
): number {
    const own = field?.n_steps ?? 0;
    return own + (fieldTakesCases(field) ? (manifest?.combination_steps?.length ?? 0) : 0);
}

/** The step a slot reads: a stored index below the field's step count, the
 *  combination at that position past it. Out of range clamps to the last
 *  stored step, as a stale index always did. */
export function slotRef(
    manifest: Pick<FeaManifest, "combination_steps"> | null | undefined,
    field: FeaManifestField | null | undefined,
    slot: number,
): FeaStepRef {
    const own = field?.n_steps ?? 0;
    if (slot < own || !fieldTakesCases(field)) {
        return {stored: Math.max(0, Math.min(slot, own - 1))};
    }
    const combos = [...(manifest?.combination_steps ?? [])].sort((a, b) => a.n - b.n);
    const c = combos[slot - own];
    return c ? {case: c.n} : {stored: Math.max(0, own - 1)};
}

/** The slot to keep when switching to ``field``: the same slot when it has
 *  one, else its last. */
export function clampSlot(
    manifest: Pick<FeaManifest, "combination_steps"> | null | undefined,
    field: FeaManifestField,
    slot: number,
): number {
    return Math.min(slot, Math.max(slotCount(manifest, field) - 1, 0));
}

/** The picker's status text for a combination. */
export function caseStatusText(info: CaseStatusInfo | null | undefined): string {
    switch (info?.status) {
        case "computing": {
            const pct = typeof info.progress === "number" && info.progress > 0
                ? ` ${Math.round(info.progress * 100)}%`
                : "";
            return `computing…${pct}`;
        }
        case "ready":
            return info.computed ? "computed" : "cached";
        case "error":
            return "failed";
        default:
            return "computed on request";
    }
}

/** The combination to warm up after ``slot`` while playing through the list:
 *  the next slot (wrapping), when it is a combination. */
export function nextCaseToPrefetch(slots: readonly CaseStepSlot[], slot: number): number | null {
    if (slots.length < 2) return null;
    const next = slots[(slot + 1) % slots.length];
    return next && next.kind === "combination" && "case" in next.ref ? next.ref.case : null;
}

/** What the colour scale is measured over, and how the legend says so. */
export type LegendScope = "all" | "stored" | "case" | "envelope";

export function legendScopeFor(args: {
    /** The manifest offers combinations (bake_version 4 with combination_steps). */
    hasCombinations: boolean;
    /** The shown slot is a combination. */
    isCase: boolean;
    /** The user asked for the range over all combinations ... */
    envelopeMode: boolean;
    /** ... and the server answered with one for this field. */
    envelopeAvailable: boolean;
}): {scope: LegendScope; label: string | null} {
    if (args.hasCombinations && args.envelopeMode && args.envelopeAvailable) {
        return {scope: "envelope", label: "over all combinations"};
    }
    if (args.isCase) return {scope: "case", label: "this case"};
    if (args.hasCombinations) return {scope: "stored", label: "over stored cases"};
    return {scope: "all", label: null};
}
