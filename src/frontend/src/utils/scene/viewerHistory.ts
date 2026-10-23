/**
 * Recording viewer operations into the undo / redo history (state/viewerHistoryStore.ts).
 *
 * `recordViewerAction(kind, fn)` wraps ONE user action: it notes the hidden ranges and the
 * selection before `fn`, runs it, and records what changed as one entry. The visibility and
 * selection helpers (utils/scene/visibility.ts, tree_view/sceneTreeActions.ts, the picking and
 * box-selection paths) wrap themselves, so every caller -- keyboard, buttons, menus, the tree's eye
 * icons -- records the same way.
 *
 * What an entry keeps:
 *  - visibility as a per-mesh diff (ranges the action hid, ranges it showed), so undo touches only
 *    those ranges and an entry costs as much as the change, not the model;
 *  - the selection before and after, by reference: the selection store replaces its map on every
 *    change, so holding the old map is free;
 *  - the info panel's name / node id, so undoing a hide brings the selection's name back with it.
 *
 * A wrapped action inside another (show all -> unhide all) records once, as the outer one. While
 * an entry is being undone or redone nothing records.
 */

import * as THREE from "three";

import {useObjectInfoStore} from "@/state/objectInfoStore";
import {requestRender} from "@/state/perfStore";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {
    describeViewerAction,
    diffSets,
    sameSelection,
    useViewerHistoryStore,
    type ViewerActionKind,
    type ViewerHistoryEntry,
} from "@/state/viewerHistoryStore";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

type Selection = ReadonlyMap<THREE.Object3D, ReadonlySet<string>>;
type Info = {name: string | null; selectedNodeId: string | null};
type HiddenDiff = {mesh: CustomBatchedMesh; added: string[]; removed: string[]};

let depth = 0;

function batchedMeshes(): CustomBatchedMesh[] {
    const out: CustomBatchedMesh[] = [];
    getViewerRuntime().scene.current?.traverse((o) => {
        if (o instanceof CustomBatchedMesh) out.push(o);
    });
    return out;
}

function captureHidden(): Map<CustomBatchedMesh, Set<string>> {
    const out = new Map<CustomBatchedMesh, Set<string>>();
    for (const mesh of batchedMeshes()) out.set(mesh, new Set(mesh.getHiddenRanges()));
    return out;
}

function captureInfo(): Info {
    const {name, selectedNodeId} = useObjectInfoStore.getState();
    return {name, selectedNodeId};
}

/** Still part of a scene: an entry must not act on a mesh that has since been unloaded. */
function attached(o: THREE.Object3D): boolean {
    let cur: THREE.Object3D = o;
    while (cur.parent) cur = cur.parent;
    return (cur as THREE.Scene).isScene === true;
}

function rangeCount(sel: Selection): number {
    let n = 0;
    for (const ids of sel.values()) n += ids.size;
    return n;
}

function restoreSelection(target: Selection): void {
    const store = useSelectedObjectStore.getState();
    if (sameSelection(store.selectedObjects, target)) return;
    store.clearSelectedObjects();
    const batch: [THREE.Object3D, string][] = [];
    for (const [obj, ids] of target) {
        if (!attached(obj)) continue;
        for (const id of ids) batch.push([obj, id]);
    }
    if (batch.length) store.addBatchofMeshes(batch);
}

function restoreInfo(info: Info): void {
    const s = useObjectInfoStore.getState();
    if (s.name !== info.name) s.setName(info.name);
    if (s.selectedNodeId !== info.selectedNodeId) s.setSelectedNodeId(info.selectedNodeId);
}

function applyHidden(diffs: readonly HiddenDiff[], direction: "undo" | "redo"): void {
    for (const {mesh, added, removed} of diffs) {
        if (!attached(mesh)) continue;
        const hide = direction === "redo" ? added : removed;
        const show = direction === "redo" ? removed : added;
        if (show.length) mesh.unhideBatchDrawRange(show);
        if (hide.length) mesh.hideBatchDrawRange(hide);
    }
}

/** A selection-only entry later actions with the same coalesce key may still extend. */
let open: {key: string; at: number; entry: ViewerHistoryEntry; extend: (sel: Selection) => void} | null = null;
/** How long after an action one with the same coalesce key still joins it. */
const COALESCE_MS = 1500;

export interface RecordOptions {
    /** Selection changes with this key, in quick succession, become one entry -- for an action that
     *  re-applies continuously (dragging a selection box's handles re-selects on every move). */
    readonly coalesce?: string;
}

/** Run `fn` as one recorded viewer action of `kind`. Returns what `fn` returns. */
export function recordViewerAction<T>(kind: ViewerActionKind, fn: () => T, opts: RecordOptions = {}): T {
    if (depth > 0 || useViewerHistoryStore.getState().applying) return fn();

    const touchesVisibility = kind !== "select" && kind !== "clear-selection";
    const hiddenBefore = touchesVisibility ? captureHidden() : null;
    const selBefore: Selection = useSelectedObjectStore.getState().selectedObjects;
    const infoBefore = captureInfo();

    depth++;
    let out: T;
    try {
        out = fn();
    } finally {
        depth--;
    }

    const diffs: HiddenDiff[] = [];
    let hidden = 0;
    let shown = 0;
    if (hiddenBefore) {
        const meshes = new Set<CustomBatchedMesh>([...hiddenBefore.keys(), ...batchedMeshes()]);
        for (const mesh of meshes) {
            const {added, removed} = diffSets(hiddenBefore.get(mesh) ?? new Set(), mesh.getHiddenRanges());
            if (!added.length && !removed.length) continue;
            diffs.push({mesh, added, removed});
            hidden += added.length;
            shown += removed.length;
        }
    }
    let selAfter: Selection = useSelectedObjectStore.getState().selectedObjects;
    const selectionChanged = !sameSelection(selBefore, selAfter);
    if (!diffs.length && !selectionChanged) return out;

    const history = useViewerHistoryStore.getState();
    const now = Date.now();
    if (
        opts.coalesce && !diffs.length && open && open.key === opts.coalesce && now - open.at < COALESCE_MS
        && history.future.length === 0 && history.past[history.past.length - 1] === open.entry
    ) {
        open.extend(selAfter);
        open.at = now;
        return out;
    }

    // The state the action leaves is read when it is first undone, not now: the click path fills
    // in the info panel's name only after a server round-trip, later than this returns.
    let infoAfter: Info | null = null;
    const entry: ViewerHistoryEntry = {
        // A getter: a coalesced entry's selection grows after it is recorded.
        get label() {
            return describeViewerAction(kind, {hidden, shown, selected: rangeCount(selAfter)});
        },
        undo: () => {
            infoAfter = captureInfo();
            applyHidden(diffs, "undo");
            restoreSelection(selBefore);
            restoreInfo(infoBefore);
            requestRender();
        },
        redo: () => {
            applyHidden(diffs, "redo");
            restoreSelection(selAfter);
            if (infoAfter) restoreInfo(infoAfter);
            requestRender();
        },
    };
    history.record(entry);
    open = opts.coalesce && !diffs.length
        ? {key: opts.coalesce, at: now, entry, extend: (sel) => { selAfter = sel; }}
        : null;
    return out;
}

/** Undo the latest viewer action. Returns its label, or null when there was none. */
export function undoViewerAction(): string | null {
    return useViewerHistoryStore.getState().undo()?.label ?? null;
}

/** Redo the latest undone viewer action. Returns its label, or null when there was none. */
export function redoViewerAction(): string | null {
    return useViewerHistoryStore.getState().redo()?.label ?? null;
}
