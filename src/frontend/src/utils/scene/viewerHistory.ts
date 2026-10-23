/**
 * Recording viewer operations into the undo / redo history (state/viewerHistoryStore.ts).
 *
 * `recordViewerAction(kind, fn)` wraps ONE user action: it notes the hidden ranges, the selection
 * and any store slices it is given (selection boxes, section planes) before `fn`, runs it, and
 * records what changed as one entry. The visibility, selection, box and plane actions wrap
 * themselves, so every caller -- keyboard, buttons, menus, the tree's eye icons, a gizmo drag --
 * records the same way. Models loaded and removed are entries too (utils/scene/modelHistory.ts).
 *
 * What an entry keeps, by names that survive a model being removed and loaded again:
 *  - visibility as a diff per MODEL (its scene name): the element ids the action hid and showed,
 *    so undo touches only those and an entry costs as much as the change, not the model;
 *  - the selection before and after, as element ids per model;
 *  - the given store slices before and after (their immutable state, by reference);
 *  - the info panel's name / node id, so undoing a hide brings the selection's name back with it.
 * Because an entry names models rather than holding their meshes, an older entry still works
 * after a later removal is undone and the model loaded again: undo reaches it only through that
 * removal's undo. Objects outside any loaded model are held by reference.
 *
 * A wrapped action inside another (show all -> unhide all) records once, as the outer one. While
 * an entry is being undone or redone nothing records.
 */

import * as THREE from "three";

import {loadedSourceGroups} from "@/state/modelState";
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
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";

type Selection = ReadonlyMap<THREE.Object3D, ReadonlySet<string>>;
type Info = {name: string | null; selectedNodeId: string | null};

/** Ids per model scene name, and per object for what is in no loaded model. */
interface ByOwner<T> {
    bySource: Map<string, T>;
    direct: Map<THREE.Object3D, T>;
}

type HiddenDiff = {added: string[]; removed: string[]};

/** A piece of store state an action changes and its undo puts back, e.g. the selection boxes. */
export interface StateSlice<T = unknown> {
    get: () => T;
    set: (value: T) => void;
}

let depth = 0;

/** A batched mesh, by what it can do rather than `instanceof`: a hot reload can leave the scene
 *  holding meshes of an earlier copy of the class. */
export function isBatchedMesh(o: unknown): o is CustomBatchedMesh {
    const m = o as CustomBatchedMesh;
    return !!o && typeof m.getHiddenRanges === "function" && typeof m.hideBatchDrawRange === "function" && m.drawRanges instanceof Map;
}

export function batchedUnder(root: THREE.Object3D): CustomBatchedMesh[] {
    const out: CustomBatchedMesh[] = [];
    root.traverse((o) => {
        if (isBatchedMesh(o)) out.push(o);
    });
    return out;
}

function sceneMeshes(): CustomBatchedMesh[] {
    const scene = getViewerRuntime().scene.current;
    return scene ? batchedUnder(scene) : [];
}

/** Each loaded model's root group, to its scene name. */
function sourceIndex(): Map<THREE.Object3D, string> {
    const out = new Map<THREE.Object3D, string>();
    for (const [name, group] of loadedSourceGroups.entries()) out.set(group, name);
    return out;
}

function sourceOf(o: THREE.Object3D, index: ReadonlyMap<THREE.Object3D, string>): string | null {
    for (let cur: THREE.Object3D | null = o; cur; cur = cur.parent) {
        const name = index.get(cur);
        if (name !== undefined) return name;
    }
    return null;
}

/** Still part of a scene: an entry must not act on an object that has since been unloaded. */
function attached(o: THREE.Object3D): boolean {
    let cur: THREE.Object3D = o;
    while (cur.parent) cur = cur.parent;
    return (cur as THREE.Scene).isScene === true;
}

/** The batched meshes drawing a model now, or one object held by reference. */
function meshesOfSource(name: string): CustomBatchedMesh[] {
    const group = loadedSourceGroups.get(name);
    return group ? batchedUnder(group) : [];
}

function captureHidden(): Map<CustomBatchedMesh, Set<string>> {
    const out = new Map<CustomBatchedMesh, Set<string>>();
    for (const mesh of sceneMeshes()) out.set(mesh, new Set(mesh.getHiddenRanges()));
    return out;
}

function captureInfo(): Info {
    const {name, selectedNodeId} = useObjectInfoStore.getState();
    return {name, selectedNodeId};
}

/** `sel` as element ids per model. */
function stableSelection(sel: Selection): ByOwner<Set<string>> {
    const index = sourceIndex();
    const out: ByOwner<Set<string>> = {bySource: new Map(), direct: new Map()};
    for (const [obj, ids] of sel) {
        if (ids.size === 0) continue;
        const source = sourceOf(obj, index);
        const map = source !== null ? out.bySource : out.direct;
        const key = (source ?? obj) as never;
        const set = (map as Map<unknown, Set<string>>).get(key) ?? new Set<string>();
        for (const id of ids) set.add(id);
        (map as Map<unknown, Set<string>>).set(key, set);
    }
    return out;
}

function countOf(sel: ByOwner<Set<string>>): number {
    let n = 0;
    for (const ids of sel.bySource.values()) n += ids.size;
    for (const ids of sel.direct.values()) n += ids.size;
    return n;
}

function restoreSelection(target: ByOwner<Set<string>>): void {
    const store = useSelectedObjectStore.getState();
    store.clearSelectedObjects();
    const batch: [THREE.Object3D, string][] = [];
    for (const [source, ids] of target.bySource) {
        for (const mesh of meshesOfSource(source)) {
            for (const id of ids) if (mesh.drawRanges.has(id)) batch.push([mesh, id]);
        }
    }
    for (const [obj, ids] of target.direct) {
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

function applyToMesh(mesh: CustomBatchedMesh, {added, removed}: HiddenDiff, direction: "undo" | "redo"): void {
    const hide = (direction === "redo" ? added : removed).filter((id) => mesh.drawRanges.has(id));
    const show = (direction === "redo" ? removed : added).filter((id) => mesh.drawRanges.has(id));
    if (show.length) mesh.unhideBatchDrawRange(show);
    if (hide.length) mesh.hideBatchDrawRange(hide);
}

function applyHidden(diffs: ByOwner<HiddenDiff>, direction: "undo" | "redo"): void {
    for (const [source, diff] of diffs.bySource) {
        for (const mesh of meshesOfSource(source)) applyToMesh(mesh, diff, direction);
    }
    for (const [obj, diff] of diffs.direct) {
        if (isBatchedMesh(obj) && attached(obj)) applyToMesh(obj, diff, direction);
    }
}

/** A selection-only (or slice-only) entry that later actions with the same coalesce key may
 *  still extend. */
let open: {key: string; at: number; entry: ViewerHistoryEntry; extend: (sel: ByOwner<Set<string>>, slices: unknown[]) => void} | null = null;
/** How long after an action one with the same coalesce key still joins it. */
const COALESCE_MS = 1500;

export interface RecordOptions {
    /** Changes with this key, in quick succession, become one entry -- for an action that
     *  re-applies continuously (dragging a selection box's or a section plane's handles). Only an
     *  action that hides or shows nothing joins. */
    readonly coalesce?: string;
    /** Store state the action changes besides visibility and selection. */
    readonly slices?: readonly StateSlice[];
}

/** Run `fn` as one recorded viewer action of `kind`. Returns what `fn` returns. */
export function recordViewerAction<T>(kind: ViewerActionKind, fn: () => T, opts: RecordOptions = {}): T {
    if (depth > 0 || useViewerHistoryStore.getState().applying) return fn();

    const slices = opts.slices ?? [];
    const touchesVisibility = kind !== "select" && kind !== "clear-selection" && !kind.startsWith("box-") && !kind.startsWith("plane-");
    const hiddenBefore = touchesVisibility ? captureHidden() : null;
    const rawSelBefore: Selection = useSelectedObjectStore.getState().selectedObjects;
    const selBefore = stableSelection(rawSelBefore);
    const slicesBefore = slices.map((s) => s.get());
    const infoBefore = captureInfo();

    depth++;
    let out: T;
    try {
        out = fn();
    } finally {
        depth--;
    }

    const diffs: ByOwner<HiddenDiff> = {bySource: new Map(), direct: new Map()};
    let hidden = 0;
    let shown = 0;
    if (hiddenBefore) {
        const index = sourceIndex();
        for (const mesh of new Set<CustomBatchedMesh>([...hiddenBefore.keys(), ...sceneMeshes()])) {
            const {added, removed} = diffSets(hiddenBefore.get(mesh) ?? new Set(), mesh.getHiddenRanges());
            if (!added.length && !removed.length) continue;
            hidden += added.length;
            shown += removed.length;
            const source = sourceOf(mesh, index);
            const map = (source !== null ? diffs.bySource : diffs.direct) as Map<unknown, HiddenDiff>;
            const key = source ?? mesh;
            const d = map.get(key) ?? {added: [], removed: []};
            d.added.push(...added);
            d.removed.push(...removed);
            map.set(key, d);
        }
    }
    const rawSelAfter: Selection = useSelectedObjectStore.getState().selectedObjects;
    const selectionChanged = !sameSelection(rawSelBefore, rawSelAfter);
    let slicesAfter = slices.map((s) => s.get());
    const slicesChanged = slicesAfter.some((v, i) => v !== slicesBefore[i]);
    const visibilityChanged = diffs.bySource.size + diffs.direct.size > 0;
    if (!visibilityChanged && !selectionChanged && !slicesChanged) return out;

    let selAfter = stableSelection(rawSelAfter);
    const history = useViewerHistoryStore.getState();
    const now = Date.now();
    if (
        opts.coalesce && !visibilityChanged && open && open.key === opts.coalesce && now - open.at < COALESCE_MS
        && history.future.length === 0 && history.past[history.past.length - 1] === open.entry
    ) {
        open.extend(selAfter, slicesAfter);
        open.at = now;
        return out;
    }

    // The state the action leaves is read when it is first undone, not now: the click path fills
    // in the info panel's name only after a server round-trip, later than this returns.
    let infoAfter: Info | null = null;
    const entry: ViewerHistoryEntry = {
        // A getter: a coalesced entry's selection grows after it is recorded.
        get label() {
            return describeViewerAction(kind, {hidden, shown, selected: countOf(selAfter)});
        },
        undo: () => {
            infoAfter = captureInfo();
            // Slices first: a restored selection box re-selects what it takes, and the selection
            // put back below must be the last word.
            slices.forEach((s, i) => s.set(slicesBefore[i]));
            applyHidden(diffs, "undo");
            restoreSelection(selBefore);
            restoreInfo(infoBefore);
            requestRender();
        },
        redo: () => {
            slices.forEach((s, i) => s.set(slicesAfter[i]));
            applyHidden(diffs, "redo");
            restoreSelection(selAfter);
            if (infoAfter) restoreInfo(infoAfter);
            requestRender();
        },
    };
    history.record(entry);
    open = opts.coalesce && !visibilityChanged
        ? {
              key: opts.coalesce,
              at: now,
              entry,
              extend: (sel, sl) => {
                  selAfter = sel;
                  slicesAfter = sl;
              },
          }
        : null;
    return out;
}

/** Undo the latest viewer action. Returns its label, or null when there was none (or one is still
 *  being applied). */
export function undoViewerAction(): string | null {
    return useViewerHistoryStore.getState().undo()?.label ?? null;
}

/** Redo the latest undone viewer action. Returns its label, or null when there was none (or one
 *  is still being applied). */
export function redoViewerAction(): string | null {
    return useViewerHistoryStore.getState().redo()?.label ?? null;
}
