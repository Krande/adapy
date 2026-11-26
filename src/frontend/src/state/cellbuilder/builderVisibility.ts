// The cell builder's cells as the hide / show commands see them, for code that must not load the builder
// (the viewport's shortcuts and commands are three.js-free, and the builder's store is not): the builder
// registers itself here when its scene is up (components/viewer/cellbuilder/initScene.ts). Nothing imported.

/** What the builder lends the hide / show commands. */
export interface BuilderVisibilityHost {
    /** Are its cells drawn now (a procedural model open, its cells shown, in a mode that shows them). */
    cellsShown(): boolean;
    /** The cells selected now (the multi-select set, else the primary selection). */
    selected(): string[];
    /** Every cell's id. */
    all(): string[];
    hide(ids: readonly string[]): void;
    unhide(ids: readonly string[]): void;
    unhideAll(): void;
    /** Swap what is shown for what is hidden ("Show complement", Alt+Q): hide `all() −
     *  hidden`, unhide `hidden` (including hidden refs), clear the selection. A double call is
     *  the identity. */
    showComplement(): void;
    /** Whether anything (a cell or a ref) is hidden now -- the "Nothing is hidden" disabled
     *  reason for `core:show-complement`. */
    anyHidden(): boolean;
}

let host: BuilderVisibilityHost | null = null;
const hostListeners = new Set<() => void>();

/** The builder lends its cells. Returns the undo. */
export function setBuilderVisibilityHost(h: BuilderVisibilityHost): () => void {
    host = h;
    hostListeners.forEach((l) => l());
    return () => {
        if (host !== h) return;
        host = null;
        hostListeners.forEach((l) => l());
    };
}

/** Called whenever the builder visibility host is lent or withdrawn (a procedural model opened,
 *  closed, or its scene remounted) -- what a caller that remembers something about "the builder's
 *  cells" (a `lastHidden` memory of the last hide) clears itself on, since a new host owns a
 *  fresh set of cells. Returns the unsubscribe function. */
export function onBuilderVisibilityHostChange(listener: () => void): () => void {
    hostListeners.add(listener);
    return () => void hostListeners.delete(listener);
}

/** The builder, while its cells are drawn; null otherwise. */
export function builderWithCellsShown(): BuilderVisibilityHost | null {
    return host && host.cellsShown() ? host : null;
}

/** The builder cells the hide / show commands act on: the selected ones while the cells are drawn; null
 *  when those commands are the scene's (nothing selected in the builder, or its cells not on screen). */
export function builderCellSelection(): string[] | null {
    const b = builderWithCellsShown();
    const ids = b?.selected() ?? [];
    return ids.length ? ids : null;
}

/** Whether the builder's cells are on screen: null when no builder scene is up (nothing says either
 *  way). The Properties panel leaves a builder pick out while they are not (a shell's mode that keeps
 *  the procedural model open behind it, the docked UI's Output). */
export function builderCellsShown(): boolean | null {
    return host ? host.cellsShown() : null;
}
