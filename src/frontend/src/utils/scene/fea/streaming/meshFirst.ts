// FEA streaming: what to open from each manifest of a bake that is still running.
//
// Mesh first: a running bake ships the mesh and a preliminary manifest long before
// it ends, then the fields as they land, then the final manifest
// (services/feaManifestPoll.ts hands each one over). Each is loaded as it comes.
// The first opens the mesh alone or with the first field to land; once something
// is painted, a later manifest keeps what the user is looking at -- the same
// field, step, component and warp scale -- rather than snapping back to the
// default field each time a field lands. A finished bake, with no preliminary
// manifest before it, opens exactly as it always did.
//
// Pure: the loader (loadWithDefaults.ts) reads the store and calls load.

import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";
import {initialStepIndex} from "../timeHistory";
import {defaultResultField} from "./defaultField";

/** What the user has open, as the FEA store holds it. */
export interface FeaOpenState {
    sourceName: string | null;
    fieldName: string | null;
    stepIndex: number;
    reduction: string;
    factor: number;
    scaleFactor: number;
}

export interface FeaOpening {
    field: FeaManifestField | null;
    stepIndex: number;
    reduction: string | null;
    displacementScale: number;
}

/** What to open from ``manifest``. ``shown``: an earlier (preliminary) manifest of
 *  the same bake has been loaded, so ``state`` is the user's view of it. */
export function feaOpening(
    manifest: FeaManifest,
    sourceName: string,
    shown: boolean,
    state: FeaOpenState,
): FeaOpening {
    const fields = manifest.fields ?? [];
    if (shown && state.sourceName === sourceName && state.fieldName) {
        const kept = fields.find((f) => f.name_canonical === state.fieldName);
        if (kept) {
            return {
                field: kept,
                stepIndex: state.stepIndex,
                reduction: state.reduction,
                displacementScale: state.factor * state.scaleFactor,
            };
        }
    }
    // The field a fresh load opens on; null opens the mesh only. See
    // defaultResultField: displacement first, never a model property.
    const field = defaultResultField(fields);
    return {
        field,
        stepIndex: initialStepIndex(field),
        reduction: field ? field.default_view?.reduction ?? "magnitude" : null,
        displacementScale: 1,
    };
}
