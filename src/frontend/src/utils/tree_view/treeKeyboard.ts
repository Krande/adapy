/**
 * Keyboard reach into the left-panel trees: copy what the Scene tree has selected, and move focus
 * into (and back out of) whichever tree is showing.
 */

import {runtime} from "@/runtime/config";
import {getSingletonViewerStores} from "@/state/AdaViewerContext";

import {revealPickInTrees} from "./revealPick";

/** The names of the rows selected in the Scene tree, TOPMOST only -- or null when that is not what
 *  a copy should mean.
 *
 *  Selecting a level in the tree selects every member under it in the scene, so copying "the
 *  selection" used to paste thousands of member names when four levels were picked. When the tree
 *  holds a selection that includes at least one LEVEL (a row with children), the rows themselves
 *  are the answer: the levels' own names, with any row whose ancestor is also selected dropped.
 *  A selection of leaves only (a 3D pick mirrored into the tree) returns null, so the caller keeps
 *  copying members exactly as before. */
export function selectedTreeRowNames(): string[] | null {
    const tree = getSingletonViewerStores().useTreeViewStore.getState().tree;
    const nodes = tree?.selectedNodes ?? [];
    if (nodes.length === 0) return null;
    if (!nodes.some((n) => !n.isLeaf)) return null;
    const ids = new Set(nodes.map((n) => n.id));
    const topmost = nodes.filter((n) => {
        for (let p = n.parent; p; p = p.parent) if (ids.has(p.id)) return false;
        return true;
    });
    const names = topmost.map((n) => String((n.data as {name?: unknown})?.name ?? "")).filter((s) => s.trim());
    return names.length ? names : null;
}

/** Close the tree drawer and hand keyboard focus back to the viewer -- Esc (or Alt+T) pressed while
 *  a tree has focus. Called from the trees' own key handlers rather than the viewer's global one:
 *  a focused tree consumes its keys, so a window listener never reliably sees them. */
export function closeTreeFromKeyboard(): void {
    const {useTreeViewStore} = getSingletonViewerStores();
    (document.activeElement as HTMLElement | null)?.blur();
    useTreeViewStore.getState().setIsTreeCollapsed(true);
    document.querySelector<HTMLElement>("canvas")?.focus();
}

/** Whether a key event inside a tree should close it: Esc alone, or Shift+T (the key that opened it). */
export function isTreeCloseKey(e: {key: string; altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean}): boolean {
    if (e.ctrlKey || e.metaKey || e.altKey) return false;
    if (e.key === "Escape") return !e.shiftKey;
    return e.shiftKey && e.key.toLowerCase() === "t";
}

/** The most recently selected element in the 3D selection, as (model key, range id), or null.
 *  The selection map keeps insertion order, so its last entry's last range is the latest pick. */
function latestSelectedElement(): {modelKey: string; rangeId: string} | null {
    const selected = getSingletonViewerStores().useSelectedObjectStore.getState().selectedObjects;
    let last: {modelKey: string; rangeId: string} | null = null;
    selected.forEach((rangeIds, obj) => {
        const o = obj as {unique_key?: string; userData?: {unique_hash?: string}};
        const modelKey = o.unique_key ?? o.userData?.unique_hash;
        let rangeId: string | undefined;
        for (const id of rangeIds) rangeId = id;
        if (modelKey && rangeId !== undefined) last = {modelKey, rangeId};
    });
    return last;
}

/** Shift+T, a toggle keyed on whether the tree is the ACTIVE thing:
 *
 *  - the tree has keyboard focus, or it is open with nothing in it to focus -> CLOSE the drawer and
 *    hand focus back to the viewer;
 *  - otherwise (closed, or open but focus elsewhere) -> open it if it is collapsed, go to the
 *    currently selected element in the tree that is showing (Scene or Sources) -- expanded down to,
 *    selected and scrolled into view, the same reveal a 3D pick does -- and put keyboard focus
 *    there, so arrow-key navigation starts from the selection. With nothing selected it focuses the
 *    tree where it was.
 *
 *  Not Ctrl+T: the browser owns that chord (new tab) and a page cannot intercept it. */
export function toggleTreeFocus(): void {
    const {useTreeViewStore, useAssetBrowserStore} = getSingletonViewerStores();
    const sources = runtime.isRestMode() && useAssetBrowserStore.getState().tab === "assets";
    const treeElement = (): HTMLElement | null =>
        sources
            ? document.querySelector<HTMLElement>('[data-testid="asset-tree"]')
            : ((useTreeViewStore.getState().tree?.listEl?.current as HTMLElement | null) ?? null);

    const active = document.activeElement;
    const current = treeElement();
    const open = !useTreeViewStore.getState().isTreeCollapsed;
    // Nothing in the showing tree to go to: no model loaded (Scene), or no rows drawn (Sources). The
    // tree can then never become the focused thing, so an open drawer must close on this key
    // directly -- or the only way out would be the mouse.
    const tv = useTreeViewStore.getState();
    const empty = sources ? !current : !tv.treeData || !tv.tree?.firstNode;
    const focused = !!current && !!active && current.contains(active);
    if (open && (focused || empty)) {
        if (focused) (active as HTMLElement).blur();
        useTreeViewStore.getState().setIsTreeCollapsed(true);
        document.querySelector<HTMLElement>("canvas")?.focus();
        return;
    }

    const go = () => {
        const pick = latestSelectedElement();
        if (pick) revealPickInTrees(pick.modelKey, pick.rangeId);
        const tree = useTreeViewStore.getState().tree;
        treeElement()?.focus();
        if (!sources && tree) {
            // Arborist's keyboard cursor is its FOCUSED row, separate from the selection: move it to
            // the selection so the arrows start there, not wherever the cursor last was.
            const row = tree.mostRecentNode ?? tree.selectedNodes[0] ?? tree.focusedNode ?? tree.firstNode;
            if (row) tree.focus(row);
        }
    };

    if (useTreeViewStore.getState().isTreeCollapsed) {
        // Opening the drawer mounts and sizes the tree; reveal once it has laid out, or the scroll
        // runs against a zero-height list.
        useTreeViewStore.getState().setIsTreeCollapsed(false);
        requestAnimationFrame(() => requestAnimationFrame(go));
    } else {
        go();
    }
}
