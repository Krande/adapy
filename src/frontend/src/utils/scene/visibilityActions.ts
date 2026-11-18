// Hide selected, Hide others, Show all and Show selected: what the commands and their keys do, on the
// viewport on screen or -- while the cell builder's cells are drawn and some are selected -- on those cells.
// One place, so the menu, the palette and the keys (whatever profile binds them) agree. No three.js here.
import {builderCellSelection, builderWithCellsShown} from "@/state/cellbuilder/builderVisibility";
import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {activeViewport} from "@/viewport/api/viewportApi";
import {refsUnder} from "@/utils/tree_view/treeRefs";

/** Hide what is selected. False when there is nothing to act on (the key is then not ours). */
export function hideSelection(): boolean {
    const cells = builderCellSelection();
    if (cells) {
        builderWithCellsShown()!.hide(cells);
        return true;
    }
    const v = activeViewport();
    if (!v) return false;
    v.hideSelected();
    return true;
}

/** Hide everything but what is selected. */
export function hideOthers(): boolean {
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
    const b = builderWithCellsShown();
    b?.unhideAll();
    const v = activeViewport();
    if (!v) return b !== null;
    v.showAll();
    return true;
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
 * viewport's selection. False when nothing is selected.
 */
export function showSelection(): boolean {
    const cells = builderCellSelection();
    if (cells) {
        builderWithCellsShown()!.unhide(cells);
        return true;
    }
    const v = activeViewport();
    if (!v) return false;
    const rows = selectedTreeRows();
    const refs = rows.length ? refsUnder(rows) : [...v.selection()];
    if (!refs.length) return false;
    v.show(refs);
    return true;
}

/** Is there anything for Hide selected / Hide others / Show selected to act on. */
export function hasVisibilitySelection(): boolean {
    if (builderCellSelection()) return true;
    if (selectedTreeRows().length) return true;
    const v = activeViewport();
    return !!v && [...v.selection()].length > 0;
}
