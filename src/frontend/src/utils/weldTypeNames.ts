// Short names for weld types (`ada.WeldType` values, as the GLB's weld metadata carries them), for
// labels where the full catalogue name is too long to read: "PARTIAL_PENETRATION_SINGLE_BEVEL_BUTT_
// PLUS_FILLET" ends in "FILLET" and reads like one at a glance.

const SHORT: Record<string, string> = {
    FILLET: "fillet",
    PARTIAL_PENETRATION_SINGLE_BEVEL_BUTT_PLUS_FILLET: "partial pen (bevel + fillet)",
    PARTIAL_PENETRATION_SQUARE_GROOVE_PLUS_FILLET: "partial pen (square + fillet)",
    SQUARE_GROOVE_SQUARE_BUTT: "square butt",
    BEVEL_GROOVE_SINGLE_V_BUTT: "single V butt",
    BEVEL_GROOVE_SINGLE_BEVEL_BUTT: "single bevel butt",
    SINGLE_V_BUTT_WITH_BROAD_ROOT_FACE: "single V butt, broad root",
    SINGLE_BEVEL_BUTT_WITH_BROAD_ROOT_FACE: "single bevel butt, broad root",
    U_GROOVE_SINGLE_U_BUTT: "single U butt",
    J_GROOVE_J_BUTT: "J butt",
    STEEP_FLANKED_BEVEL_GROOVE_SINGLE_V_BUTT: "steep V butt",
    STEEP_FLANKED_BEVEL_GROOVE_SINGLE_BEVEL_BUTT: "steep bevel butt",
    BEVEL_BACKING: "backing run",
    EDGE_FLANGE: "edge flange",
    CORNER_FLANGE: "corner flange",
    FLARE_BEVEL_GROOVE: "flare bevel",
    FLARE_V_GROOVE: "flare V",
    MELT_THROUGH: "melt-through",
    PLUG: "plug",
    SLOT: "slot",
    SPOT: "spot",
    SEAM: "seam",
    NONE: "none",
};

/** A weld type's short name; an unknown one in lower case, underscores as spaces. */
export function weldTypeName(type: string | null | undefined): string {
    if (!type) return "weld";
    const key = type.toUpperCase().replace(/^WELD_TYPE_/, "");
    return SHORT[key] ?? key.toLowerCase().replace(/_/g, " ");
}
