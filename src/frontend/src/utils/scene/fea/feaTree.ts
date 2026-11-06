// The loaded FEA result's sets and properties in the Scene tree, in every UI.
//
// `installFeaTree` (once per page; core calls it with its window mirror and on
// every FEA load) registers:
//
//   * the tree rows after the models (`registerSceneTreeRoots`): "Sets" and
//     "Properties" (`./feaTreeRows`). Selecting isolatable rows isolates them, as
//     one union; a container or an informational row (a node set) acts on
//     nothing. Through `@/layout/sceneActions`, so a pop-out's tree works too.
//   * `core:fea-tree`, the view a pop-out window draws them from (no scene there):
//     the rows, the isolation, the reasons the commands may be unavailable, and
//     what the rows say about the picked element.
//   * in the main window, the reset when the loaded result changes: the sets, the
//     memberships and the isolation belong to one result.
//
// Light on purpose (stores and pure code): the scene actions are
// `./feaIsolation`, loaded when one runs.

import type {TreeNodeData} from "@/components/tree_view/CustomNode";
import {isMirroredWindow, sceneActions} from "@/layout/sceneActions";
import {registerMirrorTopic} from "@/layout/windowMirror";
import {registerSceneTreeRoots} from "@/plugins/sceneTreeRoots";
import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useFeaIsolationStore, type FeaIsolation, type FeaPickedInfo, type FeaTreeView} from "@/state/feaIsolationStore";
import {useObjectInfoStore} from "@/state/objectInfoStore";

import {
    buildFeaTreeRoots,
    contextWireframeReason,
    elementSetRowId,
    isFeaTreeRowId,
    isIsolatableRowId,
    isolatedMembers,
    isolationReason,
    propertyFieldsOf,
    propertyLabel,
    propertyValueLabel,
    propertyValueRowId,
    setsByElement,
    type FeaSetInfo,
} from "./feaTreeRows";

/** The mirror topic of the view. */
export const FEA_TREE_TOPIC = "core:fea-tree";
/** The tree root provider's id. */
export const FEA_TREE_PROVIDER_ID = "core:fea-tree";

const store = () => useFeaIsolationStore.getState();

/** The draw ranges `isolation` keeps, against the loaded sets and memberships. */
export function feaIsolationMembers(isolation: FeaIsolation = store().isolation): string[] {
    const s = store();
    return isolatedMembers(isolation.rows, s.sets, s.membership);
}

/** Is a result loaded, with the sets that came with it? */
function loadedState(): {loaded: boolean; sets: readonly FeaSetInfo[]} {
    const {manifest, sourceName} = useFeaAnimationStore.getState();
    const s = store();
    return {loaded: !!manifest && !!sourceName && s.source === sourceName, sets: s.sets};
}

const propertyFields = () => propertyFieldsOf(useFeaAnimationStore.getState().manifest?.fields);

/** Why the isolation commands cannot act now, or null. Here: in a pop-out, the main window's. */
export function feaIsolationReason(): string | null {
    if (isMirroredWindow()) return store().mirrored?.isolationReason ?? "No FEA result is loaded";
    const {loaded, sets} = loadedState();
    return isolationReason({loaded, sets: sets.length, properties: loaded ? propertyFields().length : 0});
}

/** Why the "rest as wireframe" toggle cannot act now, or null. */
export function feaContextReason(): string | null {
    if (isMirroredWindow()) return store().mirrored?.contextReason ?? "No FEA result is loaded";
    const {loaded} = loadedState();
    const manifest = useFeaAnimationStore.getState().manifest;
    const isolation = store().isolation;
    return contextWireframeReason({
        loaded,
        hasEdges: !!manifest?.mesh?.edges_url || !!manifest?.mesh?.line_edges_url,
        isolated: isolation.rows,
        members: isolation.rows.length ? feaIsolationMembers(isolation).length : 0,
    });
}

/** The isolation in force (in a pop-out, the main window's). */
export function feaIsolationNow(): FeaIsolation {
    return isMirroredWindow() ? store().mirrored?.isolation ?? store().isolation : store().isolation;
}

let setIndex: {sets: readonly FeaSetInfo[]; byElement: Map<string, string[]>} | null = null;

/** Most sets listed for one picked element. */
export const PICKED_SETS_LIMIT = 50;

/** What the FEA rows say about element `element` (`E<label>`): its property values (read
 *  ones; `pending` counts the fields still being read) and the element sets naming it. */
export function feaPickedInfo(element: string | null | undefined): FeaPickedInfo | null {
    if (!element || !/^E\d+$/.test(element)) return null;
    const {loaded, sets} = loadedState();
    if (!loaded) return null;
    const s = store();
    const properties: FeaPickedInfo["properties"] = [];
    let pending = 0;
    for (const field of propertyFields()) {
        const map = s.membership.get(field.name_canonical);
        if (!map) {
            if (s.membershipStatus.get(field.name_canonical) !== "failed") pending++;
            continue;
        }
        const value = map.get(element);
        if (value === undefined) continue;
        properties.push({
            field: field.name_canonical,
            label: propertyLabel(field),
            value,
            valueLabel: propertyValueLabel(value, field),
            rowId: propertyValueRowId(field.name_canonical, value),
        });
    }
    if (setIndex?.sets !== sets) setIndex = {sets, byElement: setsByElement(sets)};
    const names = setIndex.byElement.get(element) ?? [];
    if (!properties.length && !pending && !names.length) return null;
    return {
        element,
        properties,
        pending,
        sets: names.slice(0, PICKED_SETS_LIMIT).map((name) => ({name, rowId: elementSetRowId(name)})),
    };
}

const EMPTY_ROOTS: readonly TreeNodeData[] = Object.freeze([]) as readonly TreeNodeData[];

let rootsMemo: {key: readonly unknown[]; roots: readonly TreeNodeData[]} | null = null;

/** The rows, the same array while nothing they show changed. */
function localRoots(): readonly TreeNodeData[] {
    const {manifest} = useFeaAnimationStore.getState();
    const s = store();
    const {loaded} = loadedState();
    const key = [loaded, manifest, s.sets, s.membership, s.membershipStatus, s.isolation];
    if (rootsMemo && rootsMemo.key.every((k, i) => k === key[i])) return rootsMemo.roots;
    const roots = loaded
        ? buildFeaTreeRoots({
              sets: s.sets,
              propertyFields: propertyFields(),
              membership: s.membership,
              membershipStatus: s.membershipStatus,
              isolated: new Set(s.isolation.rows),
          })
        : [];
    rootsMemo = {key, roots: roots.length ? roots : EMPTY_ROOTS};
    return rootsMemo.roots;
}

let viewMemo: {key: readonly unknown[]; view: FeaTreeView} | null = null;

/** What the rows, menus and commands read: computed here in the main window; in a pop-out, the main window's. */
export function feaTreeView(): FeaTreeView {
    if (isMirroredWindow()) return store().mirrored ?? EMPTY_VIEW;
    const roots = localRoots();
    const s = store();
    const picked = useObjectInfoStore.getState().name;
    const key = [roots, s.isolation, s.membership, picked];
    if (viewMemo && viewMemo.key.every((k, i) => k === key[i])) return viewMemo.view;
    const view: FeaTreeView = {
        loaded: roots.length > 0,
        roots,
        isolation: s.isolation,
        members: s.isolation.rows.length ? feaIsolationMembers().length : 0,
        contextReason: feaContextReason(),
        isolationReason: feaIsolationReason(),
        picked: feaPickedInfo(picked),
    };
    viewMemo = {key, view};
    return view;
}

const EMPTY_VIEW: FeaTreeView = Object.freeze({
    loaded: false,
    roots: EMPTY_ROOTS,
    isolation: {rows: [], contextWireframe: true},
    members: 0,
    contextReason: "No FEA result is loaded",
    isolationReason: "No FEA result is loaded",
    picked: null,
});

/** Calls `cb` whenever `feaTreeView()` may answer differently; returns the unsubscribe. */
export function subscribeFeaTreeView(cb: () => void): () => void {
    const a = useFeaIsolationStore.subscribe(cb);
    const b = useFeaAnimationStore.subscribe((s, p) => {
        if (s.manifest !== p.manifest || s.sourceName !== p.sourceName) cb();
    });
    const c = useObjectInfoStore.subscribe((s, p) => {
        if (s.name !== p.name) cb();
    });
    return () => {
        a();
        b();
        c();
    };
}

/** Read the properties' memberships once the rows (or a pick) ask for them: lazily, after the render that asked. */
let ensureQueued = false;
function ensureMembershipsSoon(): void {
    if (ensureQueued || isMirroredWindow()) return;
    const s = store();
    const missing = propertyFields().filter((f) => !s.membership.has(f.name_canonical) && !s.membershipStatus.has(f.name_canonical));
    if (!missing.length) return;
    ensureQueued = true;
    queueMicrotask(() => {
        ensureQueued = false;
        void import("./feaPropertyMembership").then((m) => m.ensurePropertyMemberships(missing));
    });
}

/** A selection that includes FEA rows: isolate the isolatable ones; true when it had any FEA row. */
export function selectFeaRows(ids: readonly string[]): boolean {
    const fea = ids.filter(isFeaTreeRowId);
    if (!fea.length) return false;
    const isolatable = fea.filter(isIsolatableRowId);
    // Containers and informational rows (node sets, a property still being read) act on nothing.
    if (isolatable.length) sceneActions().feaIsolate(isolatable, "replace");
    return true;
}

let installed = false;

/** Register the rows, the mirror topic and (in the main window) the reset on a result change. Once per page. */
export function installFeaTree(): void {
    if (installed) return;
    installed = true;
    registerSceneTreeRoots({
        id: FEA_TREE_PROVIDER_ID,
        order: 100,
        roots: () => {
            const roots = feaTreeView().roots;
            if (roots.length) ensureMembershipsSoon();
            return roots;
        },
        subscribe: subscribeFeaTreeView,
        onSelect: selectFeaRows,
    });
    registerMirrorTopic<FeaTreeView>({
        id: FEA_TREE_TOPIC,
        snapshot: () => feaTreeView(),
        subscribe: subscribeFeaTreeView,
        apply: (data) => useFeaIsolationStore.setState({mirrored: data}),
    });
    if (isMirroredWindow()) return;
    // A pick asks for the properties too: the Selected Object panel lists the element's values.
    useObjectInfoStore.subscribe((s, p) => {
        if (s.name !== p.name && s.name && /^E\d+$/.test(s.name) && loadedState().loaded) ensureMembershipsSoon();
    });
    useFeaAnimationStore.subscribe((s, p) => {
        if (s.sourceName === p.sourceName) return;
        if (store().source === s.sourceName) return;
        // Another result, or none: its sets, memberships and isolation start over. The old
        // isolation's overlays go with it (the meshes it hid are gone or replaced).
        const had = store().isolation.rows.length > 0;
        store().reset(s.sourceName);
        if (had) void import("./groupVisibility").then((m) => m.applyFeaIsolationOverlays([], false));
    });
}

/** Forget the registration -- tests only. */
export function resetFeaTreeForTests(): void {
    installed = false;
    rootsMemo = null;
    viewMemo = null;
    setIndex = null;
}
