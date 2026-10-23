/**
 * Which viewer history step a key press asks for: Ctrl+Z undoes, Ctrl+Y and Ctrl+Shift+Z redo
 * (Cmd in place of Ctrl on macOS).
 *
 * Nothing is asked for while the press lands in a text field -- an input, textarea, select or
 * contentEditable element -- so Ctrl+Z there keeps undoing the typing, as the browser does.
 */

/** An element that takes typing (and so owns Ctrl+Z itself). */
export function isTextEntryTarget(target: unknown): boolean {
    if (!target || typeof target !== "object") return false;
    const el = target as {tagName?: unknown; isContentEditable?: unknown};
    const tag = typeof el.tagName === "string" ? el.tagName.toUpperCase() : "";
    return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable === true;
}

export type UndoRedoKeyEvent = Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "shiftKey" | "altKey" | "target" | "defaultPrevented">;

export function undoRedoIntent(event: UndoRedoKeyEvent): "undo" | "redo" | null {
    // Taken by a handler before this one (the procedural cell builder has its own history).
    if (event.defaultPrevented) return null;
    if (!(event.ctrlKey || event.metaKey) || event.altKey) return null;
    if (isTextEntryTarget(event.target)) return null;
    const key = event.key.toLowerCase();
    if (key === "z") return event.shiftKey ? "redo" : "undo";
    if (key === "y" && !event.shiftKey) return "redo";
    return null;
}
