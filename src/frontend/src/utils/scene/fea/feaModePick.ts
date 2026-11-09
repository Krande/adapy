// Which (displacement field, step) a flat FEA "mode index" names. No three.js: the WebGPU embed
// and the three.js assembly (assembleFeaGlb) both use it.
import type {FeaManifest, FeaManifestField} from "@/services/viewerApi";

/** All displacement-flavoured fields in the manifest, in
 *  manifest order. Calculix / Abaqus ship one field ("U") with N
 *  steps; Code Aster ships N fields ("result__DEPL[N]") with
 *  1 step each. The unified "mode i" is found by walking the
 *  flattened (field, step) list from `pickModeEntry` below. */
export function listDisplacementFields(manifest: FeaManifest): FeaManifestField[] {
    if (!manifest || !Array.isArray(manifest.fields)) return [];
    return manifest.fields.filter((f) => {
        if (!f.blob) return false;
        const name = (f.name_canonical || "").toUpperCase();
        return (
            f.category === "displacement" ||
            name === "U" ||
            name.startsWith("U[") ||
            name.includes("DEPL") ||
            name.includes("DISPLACEMENT")
        );
    });
}

/** Pick the (field, step_within_field) pair corresponding to a
 *  caller-supplied global mode index. Walks displacement fields in
 *  manifest order and accumulates each field's n_steps until the
 *  index lands. Returns null if the manifest has no displacement
 *  fields at all. */
export function pickModeEntry(
    manifest: FeaManifest,
    globalModeIndex: number,
): {field: FeaManifestField; stepIndex: number} | null {
    const fields = listDisplacementFields(manifest);
    if (fields.length === 0) return null;
    let totalSeen = 0;
    for (const f of fields) {
        const n = Math.max(1, f.n_steps | 0);
        if (globalModeIndex < totalSeen + n) {
            return {field: f, stepIndex: globalModeIndex - totalSeen};
        }
        totalSeen += n;
    }
    // Out of range — clamp to the last (field, last-step).
    const last = fields[fields.length - 1];
    return {field: last, stepIndex: Math.max(0, (last.n_steps | 0) - 1)};
}
