// Isolating the loaded FEA result's sets and property values, on the main
// window's scene.
//
// Isolation is visibility: the isolated elements keep their own look -- the
// result colours and the legend (which measures what is drawn), the deformation
// -- and the rest is hidden, or reduced to its element edges ("rest as
// wireframe", the context). One isolation is in force, the union of the rows it
// names; each action describes the full new state (`combineIsolation`), and is
// one entry in the undo history (`recordViewerAction`), the isolation itself a
// slice of it so undo puts the rows, the context and the overlays back with the
// hidden ranges.
//
// Show / hide of a set's elements are plain range hides, recorded as hides. The
// beams drawn as lines have no range, so only an isolation reaches them.
//
// How a set becomes pixels is `./groupVisibility`'s; the arithmetic is
// `./feaTreeRows`'; the state `@/state/feaIsolationStore`; what the commands and
// rows read `./feaTree`. A pop-out window asks for these through
// `@/layout/sceneActions`.

import {useFeaIsolationStore, type FeaIsolation} from "@/state/feaIsolationStore";
import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {batchedUnder, recordViewerAction, type StateSlice} from "@/utils/scene/viewerHistory";

import {applyFeaGroupVisibility, applyFeaIsolationOverlays, clearFeaGroupVisibility} from "./groupVisibility";
import {feaIsolationMembers} from "./feaTree";
import {combineIsolation, elementSetRowId, isolatedMembers, setRowsMatching} from "./feaTreeRows";

const store = () => useFeaIsolationStore.getState();

/** The isolation as a history slice: setting it re-applies what is not a hidden range. */
const isolationSlice: StateSlice = {
    get: () => store().isolation,
    set: (raw) => {
        const value = raw as FeaIsolation;
        useFeaIsolationStore.setState({isolation: value});
        applyFeaIsolationOverlays(feaIsolationMembers(value), value.contextWireframe);
    },
};

/** Put `isolation` on the scene: hide what it does not keep (all of the rest, or down to its edges). */
function applyIsolation(isolation: FeaIsolation): void {
    const members = feaIsolationMembers(isolation);
    if (members.length === 0) clearFeaGroupVisibility();
    else applyFeaGroupVisibility(members, isolation.contextWireframe);
}

const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** Change what is isolated: `ids` (FEA row ids; others are ignored) replace it, join it or
 *  leave it. One undo entry; nothing when it does not change. */
export function isolateFeaRows(ids: readonly string[], mode: "replace" | "add" | "remove" = "replace"): void {
    const current = store().isolation;
    const rows = combineIsolation(current.rows, ids, mode);
    if (same(rows, current.rows)) return;
    const next: FeaIsolation = {rows, contextWireframe: current.contextWireframe};
    recordViewerAction(
        rows.length ? "isolate-sets" : "clear-isolation",
        () => {
            useFeaIsolationStore.setState({isolation: next});
            applyIsolation(next);
        },
        {slices: [isolationSlice]},
    );
}

/** Isolate every element set whose name holds `text` (case ignored). Returns how many. */
export function isolateFeaSetsMatching(text: string): number {
    const rows = setRowsMatching(store().sets, text);
    if (rows.length) isolateFeaRows(rows, "replace");
    return rows.length;
}

/** Isolate the element sets named `names` (programmatic: a plugin scoping the view). */
export function isolateFeaSets(names: readonly string[]): void {
    isolateFeaRows(names.map(elementSetRowId), "replace");
}

/** Show the whole model again. */
export function clearFeaIsolation(): void {
    isolateFeaRows([], "replace");
}

/** Draw the rest as a wireframe, or hide it outright; recorded, and re-applied to the isolation in force. */
export function toggleFeaContextWireframe(): void {
    const current = store().isolation;
    const next: FeaIsolation = {rows: current.rows, contextWireframe: !current.contextWireframe};
    recordViewerAction(
        "context-wireframe",
        () => {
            useFeaIsolationStore.setState({isolation: next});
            if (next.rows.length) applyFeaIsolationOverlays(feaIsolationMembers(next), next.contextWireframe);
        },
        {slices: [isolationSlice]},
    );
}

/** Hide (or show) the elements of these rows, as a plain hide: one undo entry. */
function setRowsHidden(ids: readonly string[], hide: boolean): void {
    const s = store();
    const keep = new Set(isolatedMembers(ids, s.sets, s.membership));
    const scene = getViewerRuntime().scene.current;
    if (!scene || keep.size === 0) return;
    recordViewerAction(hide ? "hide" : "show", () => {
        for (const mesh of batchedUnder(scene)) {
            const own = [...keep].filter((id) => mesh.drawRanges.has(id));
            if (!own.length) continue;
            if (hide) mesh.hideBatchDrawRange(own);
            else mesh.unhideBatchDrawRange(own);
        }
    });
    requestRender();
}

/** Hide the elements of these FEA rows. */
export const hideFeaRows = (ids: readonly string[]): void => setRowsHidden(ids, true);
/** Show the elements of these FEA rows again. */
export const showFeaRows = (ids: readonly string[]): void => setRowsHidden(ids, false);
