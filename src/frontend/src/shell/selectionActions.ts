import {copySelectionNames} from "@/utils/clipboard/copySelectionNames";
import {selectChildLevel, selectParentLevel, selectSibling} from "@/utils/tree_view/treeNavigation";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";

// Selection commands, for the menu bar and the palette.
//
// These were keyboard-only: Shift+C, Shift+↑/↓/←/→, bound in
// setupCameraControlsHandlers and documented nowhere a user would look. They are real
// commands with real handlers already — this module just gives them a second, visible
// entry point, exactly as inspectActions does for the viewport actions.
//
// Nothing is reimplemented: same copySelectionNames, same treeNavigation functions the
// key handler calls, so the two entry points cannot diverge.

/** Copy each selected object's name to the clipboard, one per line. */
export function copyNames(): void {
    void copySelectionNames(useSelectedObjectStore.getState().selectedObjects);
}

/** Move the selection up one level in the tree (never opens the outliner). */
export function selectParent(): void {
    void selectParentLevel();
}

/** Move the selection to the first child level. */
export function selectChild(): void {
    void selectChildLevel();
}

export function selectPrevSibling(): void {
    void selectSibling(-1);
}

export function selectNextSibling(): void {
    void selectSibling(1);
}

/** True when there is anything selected — menu items that act on a selection grey out. */
export function hasSelection(): boolean {
    let n = 0;
    useSelectedObjectStore.getState().selectedObjects.forEach((r) => (n += r.size));
    return n > 0;
}
