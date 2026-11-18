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
}

let host: BuilderVisibilityHost | null = null;

/** The builder lends its cells. Returns the undo. */
export function setBuilderVisibilityHost(h: BuilderVisibilityHost): () => void {
    host = h;
    return () => {
        if (host === h) host = null;
    };
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
