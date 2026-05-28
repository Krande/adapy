// Reverse weld-index for the selection inspector. Built once per GLB
// load from the per-weld entries in `ADA_EXT_data.design_objects[]
// .object_metadata`; lets the inspector show "Welds (N)" for any
// selected beam/plate without an extra server round-trip.
//
// Forward edge (weld → list[member name]) lives in the GLB metadata
// itself (Stage 6). This store inverts it so the inspector can render
// the panel keyed by the currently-selected member.

import {create} from "zustand";

/** One weld touching the selected member. Carries the weld's name +
 *  display fields + the names of all *other* members it joins, so
 *  the inspector can render a row and offer click-to-navigate
 *  partner buttons without re-reading the GLB. */
export interface WeldRef {
    weldName: string;
    weldType: string | null;
    throat: number | null;
    sided: string | null;
    /** Member names this weld joins *other than* the selected one. */
    partners: string[];
}

type WeldGraphState = {
    /** Member name → list of welds touching it. */
    indexByMember: Map<string, WeldRef[]>;
    setIndex: (idx: Map<string, WeldRef[]>) => void;
    clearIndex: () => void;
};

export const useWeldGraphStore = create<WeldGraphState>((set) => ({
    indexByMember: new Map(),
    setIndex: (indexByMember) => set({indexByMember}),
    clearIndex: () => set({indexByMember: new Map()}),
}));

/** Build the reverse member→weld index from the asset-level extension.
 *
 * Walks every `design_object`'s `object_metadata`, picks out entries
 * tagged ``type === "weld"``, and for each one indexes it under every
 * member name it touches. `partners` excludes the self-member so the
 * inspector can render "click to swap selection" buttons directly.
 *
 * Returns an empty Map for extensions with no welds (CAD models that
 * predate Stage 6, FEA-only models, plain Parts, etc.).
 */
export function buildWeldIndex(adaExt: unknown): Map<string, WeldRef[]> {
    const out = new Map<string, WeldRef[]>();
    const designObjects = (adaExt as {design_objects?: unknown[]})?.design_objects;
    if (!Array.isArray(designObjects)) return out;

    for (const obj of designObjects) {
        const meta = (obj as {object_metadata?: Record<string, unknown>})?.object_metadata;
        if (!meta || typeof meta !== "object") continue;

        for (const [weldName, raw] of Object.entries(meta)) {
            const entry = raw as Record<string, unknown> | null;
            if (!entry || entry.type !== "weld") continue;
            const members = entry.members;
            if (!Array.isArray(members)) continue;

            const memberNames = members.filter((m): m is string => typeof m === "string");
            for (const member of memberNames) {
                const partners = memberNames.filter((m) => m !== member);
                const ref: WeldRef = {
                    weldName,
                    weldType: typeof entry.weld_type === "string" ? entry.weld_type : null,
                    throat: typeof entry.throat === "number" ? entry.throat : null,
                    sided: typeof entry.sided === "string" ? entry.sided : null,
                    partners,
                };
                const bucket = out.get(member);
                if (bucket) bucket.push(ref);
                else out.set(member, [ref]);
            }
        }
    }
    return out;
}
