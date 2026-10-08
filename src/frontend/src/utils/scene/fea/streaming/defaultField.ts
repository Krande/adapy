// FEA streaming: which field a freshly opened result shows first.
//
// Pure, so it tests without the loader.

import type {FeaManifestField} from "@/services/viewerApi";

/**
 * The field a fresh load opens on, or null to open the mesh only.
 *
 * Displacement first: it is what most people want to see first, and it is the
 * warp source for everything else. Otherwise the first renderable field (nodal
 * or element), for stress-only output.
 *
 * Never a model property (category "property": thickness, material, section).
 * Those say what the model IS and are painted on request by whatever lists
 * them. Opened as the default they became "the result" of a bake that has none
 * - a model-only bake carries nothing else - so the result pickers offered
 * MATERIAL as the component and the legend named it as the load case. A bake
 * whose only fields are properties opens like one with no fields at all.
 */
export function defaultResultField(
    fields: readonly FeaManifestField[] | null | undefined,
): FeaManifestField | null {
    const results = (fields ?? []).filter((f) => f.category !== "property");
    return results.find((f) => f.category === "displacement") ?? results[0] ?? null;
}
