// Which gizmo may act on which kind of builder selection.
//
// These rules are NOT invented here. CellBuilderController binds them to G/R/S:
//
//     if (!ev.shiftKey && cell && cell.kind !== "loft") {
//         if (k === "g")                                  → translate
//         if (k === "r" && cell.kind === "equipment")      → rotate
//         if (k === "s" && cell.kind === "cell")           → resize
//     }
//
// A cell has no meaningful rotation; equipment has no resize handles; a loft is driven
// by its profile rather than by a gizmo. The mode toolbar has to mirror that exactly, or
// a button lights up and sets a gizmo mode the controller then refuses to act on — worse
// than an honestly greyed button, and worse still when the same key correctly declines.
//
// Pure and separate so the mirroring is asserted rather than assumed: the two live in
// different files, and this is precisely the sort of duplicated rule that drifts.

/** Selection kinds the builder recognises. Mirrors cellBuilderStore's `kind`. */
export type BuilderKind = "cell" | "equipment" | "loft" | (string & {});

export interface GizmoContext {
    /** Is a procedural model open at all. */
    modelOpen: boolean;
    /** Kind of the current selection, or null when nothing is selected. */
    selectionKind: BuilderKind | null;
}

/** Kinds each gizmo accepts. `null` means "any kind except loft". */
export const GIZMO_KINDS: Record<"translate" | "rotate" | "resize", readonly string[] | null> = {
    translate: null,
    rotate: ["equipment"],
    resize: ["cell"],
};

/** Null when the gizmo can act; otherwise the reason to show in the tooltip. */
export function gizmoReason(
    gizmo: "translate" | "rotate" | "resize",
    ctx: GizmoContext,
): string | null {
    if (!ctx.modelOpen) return "No procedural model is open";
    if (ctx.selectionKind == null) return "Nothing is selected";
    if (ctx.selectionKind === "loft") return "Not available for a loft";

    const kinds = GIZMO_KINDS[gizmo];
    if (kinds && !kinds.includes(ctx.selectionKind)) return `Only for ${kinds.join(" or ")}`;
    return null;
}
