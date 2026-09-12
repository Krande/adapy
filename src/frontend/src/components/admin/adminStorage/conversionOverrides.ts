// Per-conversion overrides applied to every Convert click on the admin
// storage tab. "unset" omits the key, so the global setting wins.

export type OverrideKey =
    | "use_sat_pcurves"
    | "skip_shapefix"
    | "merge_meshes"
    | "profile_conversions";

export type OverrideTri = "unset" | "on" | "off";

export const OVERRIDE_KEYS: { key: OverrideKey; label: string }[] = [
    {key: "use_sat_pcurves", label: "Use SAT pcurves"},
    {key: "skip_shapefix", label: "Skip ShapeFix"},
    {key: "merge_meshes", label: "Merge GLB meshes"},
    {key: "profile_conversions", label: "Profile this run"},
];

export function buildConversionOptions(
    o: Record<OverrideKey, OverrideTri>,
): Partial<Record<OverrideKey, boolean | null>> | undefined {
    const out: Partial<Record<OverrideKey, boolean | null>> = {};
    let any = false;
    for (const k of Object.keys(o) as OverrideKey[]) {
        const v = o[k];
        if (v === "on") {
            out[k] = true;
            any = true;
        } else if (v === "off") {
            out[k] = false;
            any = true;
        }
        // "unset" → omit the key, so the global setting wins.
    }
    return any ? out : undefined;
}
