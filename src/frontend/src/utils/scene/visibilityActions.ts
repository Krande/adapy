// Hide selected, Hide others, Show all, Show selected and Show complement: what the commands and
// their keys do, on the viewport on screen or -- while the cell builder's cells are drawn and some
// are selected -- on those cells. One place, so the menu, the palette and the keys (whatever
// profile binds them) agree. No three.js here.
import {
    builderCellSelection,
    builderWithCellsShown,
    onBuilderVisibilityHostChange,
} from "@/state/cellbuilder/builderVisibility";
import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {activeViewport, onActiveViewportChange, type ObjectRef} from "@/viewport/api/viewportApi";
import {refsUnder} from "@/utils/tree_view/treeRefs";

/** What `hideSelection` just hid, so an immediate `showSelection` with nothing newly selected
 *  (the GeniE profile's Alt+Minus then Alt+Plus pair) restores exactly that, as one undo step. Cleared by
 *  `showSelection` itself (restoring it is a one-shot memory, not a toggle), by `hideOthers` /
 *  `showAll` / `showComplement` (each leaves a DIFFERENT set hidden, so the memory of the narrower
 *  hide is stale), and whenever the viewport or the builder host changes -- otherwise it could name
 *  ids/refs of a surface that is no longer the one on screen. */
type LastHidden = {kind: "builder"; ids: string[]} | {kind: "viewport"; refs: ObjectRef[]};
let lastHidden: LastHidden | null = null;

onActiveViewportChange(() => void (lastHidden = null));
onBuilderVisibilityHostChange(() => void (lastHidden = null));

/** Hide what is selected. False when there is nothing to act on (the key is then not ours). */
export function hideSelection(): boolean {
    const cells = builderCellSelection();
    if (cells) {
        builderWithCellsShown()!.hide(cells);
        lastHidden = {kind: "builder", ids: [...cells]};
        return true;
    }
    const v = activeViewport();
    if (!v) return false;
    const refs = [...v.selection()];
    v.hideSelected();
    if (refs.length) lastHidden = {kind: "viewport", refs};
    return true;
}

/** Hide everything but what is selected. */
export function hideOthers(): boolean {
    lastHidden = null; // a DIFFERENT set is now hidden; the narrower memory is stale.
    const cells = builderCellSelection();
    if (cells) {
        const b = builderWithCellsShown()!;
        const keep = new Set(cells);
        b.hide(b.all().filter((id) => !keep.has(id)));
        return true;
    }
    const v = activeViewport();
    if (!v) return false;
    v.hideOthers();
    return true;
}

/** Show everything again: the builder's cells too while they are drawn. */
export function showAll(): boolean {
    lastHidden = null; // nothing is hidden any more.
    const b = builderWithCellsShown();
    b?.unhideAll();
    const v = activeViewport();
    if (!v) return b !== null;
    v.showAll();
    return true;
}

/** Swap what is shown for what is hidden (GeniE's "Show complement", Alt+Q / `core:show-complement`):
 *  the builder's cells (and refs) while they are drawn, else the viewport's. False when the active
 *  surface has no complement to show (a viewport whose renderer does not implement `showComplement`). */
export function showComplement(): boolean {
    lastHidden = null; // a DIFFERENT set is now hidden; the narrower memory is stale.
    const b = builderWithCellsShown();
    if (b) {
        b.showComplement();
        return true;
    }
    const v = activeViewport();
    if (!v?.showComplement) return false;
    v.showComplement();
    return true;
}

/** Is there anything hidden for `showComplement` to swap -- the "Nothing is hidden" disabled reason
 *  for `core:show-complement`. */
export function hasHiddenToComplement(): boolean {
    const b = builderWithCellsShown();
    if (b) return b.anyHidden();
    return !!activeViewport()?.hasHidden?.();
}

let treeRows: (() => readonly TreeNodeData[]) | null = null;

/** Where the tree's selected rows are read (the tree view store says, when it loads: importing it here
 *  would pull the tree's components, and three.js with them, into the three.js-free viewport). */
export function setSelectedTreeRowsSource(source: () => readonly TreeNodeData[]): void {
    treeRows = source;
}

/** The tree's selected rows (a level row stands for everything under it). */
function selectedTreeRows(): readonly TreeNodeData[] {
    return treeRows?.() ?? [];
}

/**
 * Show what is selected again (GeniE's "Add selection"): the builder's selected cells, or the tree's selected
 * rows with everything under them -- select a level in the tree and its hidden elements come back -- else the
 * viewport's selection. With no explicit selection, restores what `hideSelection` just hid as one undo
 * step, then forgets it (a one-shot memory, not a toggle -- a second press with still nothing selected has
 * nothing left to restore). False when there is neither a selection nor a memory of a last hide.
 */
export function showSelection(): boolean {
    const cells = builderCellSelection();
    if (cells) {
        builderWithCellsShown()!.unhide(cells);
        lastHidden = null;
        return true;
    }
    const v = activeViewport();
    if (v) {
        const rows = selectedTreeRows();
        const refs = rows.length ? refsUnder(rows) : [...v.selection()];
        if (refs.length) {
            v.show(refs);
            lastHidden = null;
            return true;
        }
    }
    // An explicit selection (above) always wins; only with none does the memory of the last hide
    // apply.
    if (!lastHidden) return false;
    const restore = lastHidden;
    lastHidden = null;
    if (restore.kind === "builder") {
        const b = builderWithCellsShown();
        if (!b) return false;
        b.unhide(restore.ids);
        return true;
    }
    if (!v) return false;
    v.show(restore.refs);
    return true;
}

/** Is there anything for Hide selected / Hide others / Show selected to act on. */
export function hasVisibilitySelection(): boolean {
    if (builderCellSelection()) return true;
    if (selectedTreeRows().length) return true;
    const v = activeViewport();
    return !!v && [...v.selection()].length > 0;
}

/** Is there anything for `showSelection`'s DISABLED check: a selection, or a memory of the last
 *  hide to restore. */
export function hasShowTarget(): boolean {
    return hasVisibilitySelection() || lastHidden !== null;
}
