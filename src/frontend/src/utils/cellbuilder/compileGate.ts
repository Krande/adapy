/**
 * Pure gate for the procedural preview compile (Compile button / ⇧↵) — no
 * zustand, no three.js, node-testable. Kept out of cellBuilderStore.ts so tests
 * don't pull the store's whole dependency graph.
 */

export interface CompileGateState {
    /** Uncommitted changes since the last save-point. */
    dirty: boolean;
    /** Which LOD(s) a Compile produces. */
    buildSim: boolean;
    buildDetail: boolean;
    /** Scene source of the loaded simulation result, or null when none is shown
     * (covers the side-by-side secondary view — same source). */
    resultSourceName: string | null;
    /** Scene source of the loaded detail result, or null when none is shown. */
    detailSourceName: string | null;
}

/**
 * Whether a preview compile should do anything. True when there are uncommitted
 * changes, OR when a wanted result isn't currently in the scene — so pressing
 * ⇧↵ on an unchanged model that has no simulation/detail loaded still produces
 * one (rather than a no-op). Once the wanted result(s) are loaded and nothing
 * has changed it's false; the ▾ menu's force-recompile is the rebuild escape
 * hatch for that case.
 */
export function needsPreviewCompile(s: CompileGateState): boolean {
    if (s.dirty) return true;
    const simWanted = s.buildSim || !s.buildDetail; // mirrors compilePreviewSelected
    const detailWanted = s.buildDetail;
    const simMissing = simWanted && s.resultSourceName === null;
    const detailMissing = detailWanted && s.detailSourceName === null;
    return simMissing || detailMissing;
}
