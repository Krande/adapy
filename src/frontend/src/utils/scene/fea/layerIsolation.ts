// Isolating the sets and property values of an FEA result drawn through result layers.
//
// The same actions as `./feaIsolation` (which acts on the three.js mesh), for a result whose
// elements are objects of the viewport: a set is a list of element ids, and isolating, hiding
// and showing it go through the viewport's interface. The state is the same store
// (`@/state/feaIsolationStore`), so the Scene tree's rows, the commands and the Selected Object
// panel read one isolation whichever way the result is drawn.
//
// What the three.js path does that this does not: the beams drawn as coloured lines have no
// object here, so an isolation does not reach them, and there is no undo on this viewport.
//
// No three.js in this module or in anything it imports.

import {useFeaAnimationStore} from "@/state/feaAnimationStore";
import {useFeaIsolationStore, type FeaIsolation} from "@/state/feaIsolationStore";
import {useFemConceptsStore} from "@/state/femConceptsStore";
import {activeViewport, type ObjectRef} from "@/viewport/api/viewportApi";

import {combineIsolation, elementSetRowId, isolatedMembers, setRowsMatching} from "./feaTreeRows";
import {layerResultObjects, layerSessionActive, setLayerHiddenEdgesKept} from "./streaming/layerSession";

const store = () => useFeaIsolationStore.getState();

/** Is the result on screen one drawn through result layers: the one these actions act on. */
export const layerResultShown = (): boolean => layerSessionActive();

/** `ids` as objects of the viewport: one ref, given new values for each. */
function* refsOf(model: string, ids: Iterable<string>): Generator<ObjectRef> {
    const ref: ObjectRef = {model, id: ""};
    for (const id of ids) {
        ref.id = id;
        yield ref;
    }
}

/** `ids` without those `drop` names; `ids` itself when there is nothing to drop. */
function* without(ids: Iterable<string>, drop: ReadonlySet<string> | null): Iterable<string> {
    for (const id of ids) if (!drop?.has(id)) yield id;
}

let known: {ids: readonly string[]; set: ReadonlySet<string>} | null = null;

/** The beams that only carry a mass to its centre of gravity, unless they are asked for. */
function hiddenDummies(): ReadonlySet<string> | null {
    const {dummyMassIds, showDummyMasses} = useFemConceptsStore.getState();
    return showDummyMasses || dummyMassIds.length === 0 ? null : new Set(dummyMassIds);
}

/** Put `isolation` on the viewport: its elements shown and the rest hidden (their edges kept
 *  when the rest is drawn as a wireframe), or everything shown when it names nothing the result
 *  has. The dummy-mass beams stay hidden either way unless they are asked for. */
function applyIsolation(isolation: FeaIsolation): void {
    const viewport = activeViewport();
    const objects = layerResultObjects();
    if (!viewport || !objects) return;
    const s = store();
    const members = isolatedMembers(isolation.rows, s.sets, s.membership);
    if (known?.ids !== objects.ids) known = {ids: objects.ids, set: new Set(objects.ids)};
    const has = known.set;
    const dummies = hiddenDummies();
    // A selection that names nothing the result has leaves it whole rather than blanking the view.
    let matches = false;
    for (const id of members) {
        if (has.has(id)) {
            matches = true;
            break;
        }
    }
    if (matches) {
        viewport.isolate(refsOf(objects.model, without(members, dummies)));
    } else {
        viewport.showAll();
        if (dummies) viewport.hide(refsOf(objects.model, dummies));
    }
    setLayerHiddenEdgesKept(matches && isolation.contextWireframe);
}

/** The isolation in force and the dummy-mass switch, applied again: after a result loaded, after
 *  the switch changed, and after the beam sections came or went (a beam is an object of the
 *  viewport only while something draws it). */
export function applyLayerIsolation(): void {
    if (!layerSessionActive()) return;
    const isolation = store().isolation;
    if (isolation.rows.length) return applyIsolation(isolation);
    // Nothing isolated: only the dummy-mass beams change, whatever else the user has hidden.
    const viewport = activeViewport();
    const objects = layerResultObjects();
    const {dummyMassIds, showDummyMasses} = useFemConceptsStore.getState();
    if (!viewport || !objects || dummyMassIds.length === 0) return;
    if (showDummyMasses) viewport.show(refsOf(objects.model, dummyMassIds));
    else viewport.hide(refsOf(objects.model, dummyMassIds));
}

const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x, i) => x === b[i]);

/** Change what is isolated: `ids` (FEA row ids; others are ignored) replace it, join it or leave it. */
export function isolateFeaRows(ids: readonly string[], mode: "replace" | "add" | "remove" = "replace"): void {
    const current = store().isolation;
    const rows = combineIsolation(current.rows, ids, mode);
    if (same(rows, current.rows)) return;
    const next: FeaIsolation = {rows, contextWireframe: current.contextWireframe};
    useFeaIsolationStore.setState({isolation: next});
    applyIsolation(next);
}

/** Isolate every element set whose name holds `text` (case ignored). Returns how many. */
export function isolateFeaSetsMatching(text: string): number {
    const rows = setRowsMatching(store().sets, text);
    if (rows.length) isolateFeaRows(rows, "replace");
    return rows.length;
}

/** Isolate the element sets named `names`. */
export function isolateFeaSets(names: readonly string[]): void {
    isolateFeaRows(names.map(elementSetRowId), "replace");
}

/** Show the whole model again. */
export function clearFeaIsolation(): void {
    isolateFeaRows([], "replace");
}

/** Draw the rest as a wireframe, or hide it outright. */
export function toggleFeaContextWireframe(): void {
    const current = store().isolation;
    const next: FeaIsolation = {rows: current.rows, contextWireframe: !current.contextWireframe};
    useFeaIsolationStore.setState({isolation: next});
    if (next.rows.length) applyIsolation(next);
}

function setRowsHidden(ids: readonly string[], hide: boolean): void {
    const viewport = activeViewport();
    const objects = layerResultObjects();
    const s = store();
    const members = isolatedMembers(ids, s.sets, s.membership);
    if (!viewport || !objects || members.length === 0) return;
    if (hide) viewport.hide(refsOf(objects.model, members));
    else viewport.show(refsOf(objects.model, without(members, hiddenDummies())));
}

/** Hide the elements of these FEA rows. */
export const hideFeaRows = (ids: readonly string[]): void => setRowsHidden(ids, true);
/** Show the elements of these FEA rows again. */
export const showFeaRows = (ids: readonly string[]): void => setRowsHidden(ids, false);

let following = false;

/** Keep the isolation and the dummy-mass switch applied while a result is drawn through
 *  layers: called once a result's sets are registered. */
export function followLayerIsolation(): void {
    applyLayerIsolation();
    if (following) return;
    following = true;
    // The beam sections came or went: after the session has made or hidden their layer.
    useFeaAnimationStore.subscribe((now, before) => {
        if (now.beamSolidsVisible !== before.beamSolidsVisible && layerSessionActive()) setTimeout(applyLayerIsolation, 0);
    });
    useFemConceptsStore.subscribe((now, before) => {
        if (now.showDummyMasses !== before.showDummyMasses || now.dummyMassIds !== before.dummyMassIds) applyLayerIsolation();
    });
}
