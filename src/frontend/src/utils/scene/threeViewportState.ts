/**
 * The three.js viewport's undoable state: what the undo / redo history (./viewerHistory.ts) captures
 * before an action, measures after it and puts back on undo and redo (viewport/api/viewportState.ts).
 *
 * What a state keeps, by names that survive a model being removed and loaded again:
 *  - visibility as a diff per MODEL (its scene name): the element ids the action hid and showed,
 *    so undo touches only those and an entry costs as much as the change, not the model;
 *  - the selection, as element ids per model.
 * Because a state names models rather than holding their meshes, an older entry still works
 * after a later removal is undone and the model loaded again: undo reaches it only through that
 * removal's undo. Objects outside any loaded model are held by reference.
 */

import type * as THREE from "three";

import {loadedSourceGroups} from "@/state/modelState";
import {useSelectedObjectStore} from "@/state/useSelectedObjectStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {diffSets, sameSelection} from "@/state/viewerHistoryStore";
import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import type {ViewportState, ViewportStateChange, ViewportStateParts} from "@/viewport/api/viewportState";

type Selection = ReadonlyMap<THREE.Object3D, ReadonlySet<string>>;

/** Ids per model scene name, and per object for what is in no loaded model. */
interface ByOwner<T> {
    bySource: Map<string, T>;
    direct: Map<THREE.Object3D, T>;
}

type HiddenDiff = {added: string[]; removed: string[]};

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

/** One end of a step. The state before an action also holds what a measurement of the action
 *  needs (the hidden ranges per mesh as they were, the selection store's own map); both ends share
 *  the step's visibility diff once it has been measured. */
class ThreeState implements ViewportState {
    readonly viewport = "three";
    /** What the step between this state and its other end hid and showed; null until measured. */
    diffs: ByOwner<HiddenDiff> | null = null;

    constructor(
        readonly end: "before" | "after",
        /** The hidden ranges per mesh, when visibility was asked for (the "before" end only). */
        readonly hiddenThen: Map<CustomBatchedMesh, Set<string>> | null,
        /** The selection store's map at the time, compared by reference first. */
        readonly raw: Selection,
        /** The selection as element ids per model; null when the selection was not asked for. */
        readonly selection: ByOwner<Set<string>> | null,
    ) {}

    get selected(): number {
        return this.selection ? countOf(this.selection) : 0;
    }
}

const isThreeState = (state: ViewportState): state is ThreeState => (state as ThreeState).viewport === "three";

export function captureThreeState(what: ViewportStateParts): ViewportState {
    const raw: Selection = useSelectedObjectStore.getState().selectedObjects;
    return new ThreeState("before", what.visibility ? captureHidden() : null, raw, what.selection ? stableSelection(raw) : null);
}

export function threeStateChange(before: ViewportState): ViewportStateChange {
    if (!isThreeState(before)) throw new Error("a state of another viewport");
    const diffs: ByOwner<HiddenDiff> = {bySource: new Map(), direct: new Map()};
    let hidden = 0;
    let shown = 0;
    const hiddenBefore = before.hiddenThen;
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
    const rawAfter: Selection = useSelectedObjectStore.getState().selectedObjects;
    return {
        selection: before.selection !== null && !sameSelection(before.raw, rawAfter),
        visibility: diffs.bySource.size + diffs.direct.size > 0,
        hidden,
        shown,
        after: () => {
            const after = new ThreeState("after", null, rawAfter, before.selection ? stableSelection(rawAfter) : null);
            before.diffs = diffs;
            after.diffs = diffs;
            return after;
        },
    };
}

export function restoreThreeState(state: ViewportState): void {
    if (!isThreeState(state)) return;
    if (state.diffs) applyHidden(state.diffs, state.end === "before" ? "undo" : "redo");
    if (state.selection) restoreSelection(state.selection);
}
