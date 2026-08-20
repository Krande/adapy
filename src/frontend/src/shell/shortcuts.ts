// The one description of every keyboard shortcut.
//
// Before this there were three: the actual bindings in setupCameraControlsHandlers, a
// hand-written cheat sheet in ShortcutsModal, and whatever each tooltip happened to say.
// Three copies of the same facts drift, and the two that are only documentation drift
// silently — a tooltip promising Shift+G is wrong forever and nothing notices.
//
// This module is the source; the command palette, rail tooltips and docs/SHORTCUTS.md all
// read it. `shortcuts.test.ts` cross-checks the GLOBAL entries against what
// setupCameraControlsHandlers actually binds, by parsing that file — so a binding added
// or removed there without updating this list fails CI.
//
// DEPENDENCY-FREE: no React, no stores. It is data.

export type ShortcutScope =
    /** Fires anywhere except a text input. Bound in setupCameraControlsHandlers. */
    | "global"
    /** Only while the cellbuilder tool is active (keyed off cellBuilderStore.active). */
    | "builder"
    /** Only in gallery walk mode. */
    | "gallery"
    /** Shell chrome — bound by the shell itself. */
    | "shell";

export interface Shortcut {
    id: string;
    /** Display form, e.g. "Shift+A". The canonical spelling used everywhere. */
    keys: string;
    label: string;
    scope: ShortcutScope;
    /** Grouping for the reference and the palette. */
    group: string;
}

export const SHORTCUTS: readonly Shortcut[] = [
    // ---- global: view -----------------------------------------------------
    {id: "fit-all", keys: "Shift+A", label: "Fit all to view", scope: "global", group: "View"},
    {id: "focus-selection", keys: "Shift+F", label: "Centre on selection", scope: "global", group: "View"},
    {id: "hide-selection", keys: "Shift+H", label: "Hide selection", scope: "global", group: "View"},
    {id: "unhide-all", keys: "Shift+U", label: "Unhide all", scope: "global", group: "View"},

    // ---- global: panels ---------------------------------------------------
    {id: "toggle-options", keys: "Shift+Q", label: "Toggle preferences", scope: "global", group: "Panels"},
    {id: "toggle-tree", keys: "Shift+T", label: "Toggle the outliner", scope: "global", group: "Panels"},

    // ---- global: selection ------------------------------------------------
    {id: "copy-names", keys: "Shift+C", label: "Copy selected names", scope: "global", group: "Selection"},
    {id: "tree-parent", keys: "Shift+Up", label: "Select parent level", scope: "global", group: "Selection"},
    {id: "tree-child", keys: "Shift+Down", label: "Select first child", scope: "global", group: "Selection"},
    {id: "tree-prev", keys: "Shift+Left", label: "Select previous sibling", scope: "global", group: "Selection"},
    {id: "tree-next", keys: "Shift+Right", label: "Select next sibling", scope: "global", group: "Selection"},

    // ---- global: build ----------------------------------------------------
    {id: "compile-preview", keys: "Shift+Enter", label: "Compile preview", scope: "global", group: "Build"},

    // ---- builder tool -----------------------------------------------------
    // Tool-scoped, NOT mode-scoped: they key off cellBuilderStore.active, and the
    // non-modality contract says a mode must never gate them.
    {id: "gizmo-move", keys: "G", label: "Move gizmo", scope: "builder", group: "Build"},
    {id: "gizmo-rotate", keys: "R", label: "Rotate gizmo", scope: "builder", group: "Build"},
    {id: "gizmo-scale", keys: "S", label: "Resize gizmo", scope: "builder", group: "Build"},
    {id: "axis-x", keys: "X", label: "Lock to X axis", scope: "builder", group: "Build"},
    {id: "axis-y", keys: "Y", label: "Lock to Y axis", scope: "builder", group: "Build"},
    {id: "axis-z", keys: "Z", label: "Lock to Z axis", scope: "builder", group: "Build"},
    {id: "builder-undo", keys: "Ctrl+Z", label: "Undo", scope: "builder", group: "Build"},
    {id: "builder-redo", keys: "Shift+Z", label: "Redo", scope: "builder", group: "Build"},
    {id: "builder-accept", keys: "Enter", label: "Accept the current move / rotate / resize", scope: "builder", group: "Build"},
    {id: "builder-back", keys: "Escape", label: "Step back one layer (cancels an axis-locked move)", scope: "builder", group: "Build"},

    // ---- gallery ----------------------------------------------------------
    {id: "gallery-prev", keys: "Left", label: "Previous item", scope: "gallery", group: "Gallery"},
    {id: "gallery-next", keys: "Right", label: "Next item", scope: "gallery", group: "Gallery"},

    // ---- shell ------------------------------------------------------------
    {id: "command-palette", keys: "Ctrl+K", label: "Open the command palette", scope: "shell", group: "Shell"},
    // Alias, for environments where the browser claims Ctrl+K first (and because it is
    // the convention most users already have in their fingers).
    {id: "command-palette-alt", keys: "Ctrl+Shift+P", label: "Open the command palette", scope: "shell", group: "Shell"},
] as const;

export const shortcutFor = (id: string): Shortcut | undefined => SHORTCUTS.find((s) => s.id === id);

/** Display keys for an id, or undefined. Used by tooltips so they cannot invent one. */
export const keysFor = (id: string): string | undefined => shortcutFor(id)?.keys;

export const SHORTCUT_GROUPS = [...new Set(SHORTCUTS.map((s) => s.group))];

export const shortcutsInGroup = (group: string): Shortcut[] =>
    SHORTCUTS.filter((s) => s.group === group);

/**
 * The global bindings, in the normalised form the test compares against the real handler:
 * lower-cased key names with the shift flag split out.
 *
 * Exported for the test rather than kept private, because the whole point is that an
 * independent reader can check this list against the source of truth.
 */
export function globalBindings(): {shift: boolean; key: string}[] {
    return SHORTCUTS.filter((s) => s.scope === "global").map((s) => {
        const parts = s.keys.split("+");
        const key = parts[parts.length - 1].toLowerCase();
        return {shift: parts.some((p) => p.toLowerCase() === "shift"), key: key.replace(/^arrow/, "")};
    });
}
