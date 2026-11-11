// Undo / redo on the three.js-free viewport: its state as the history captures and restores it
// (viewport/api/viewportState.ts), and each of its operations that changes the selection or what is
// hidden made one undo step (utils/scene/viewerHistory.ts), as on the three.js viewport.
//
// The scene keeps two sets of object keys, selected and hidden. A state is the selected keys as a copy
// and, for a step that hid or showed something, the keys it hid and the keys it showed: undo and redo
// move only those, so an entry costs as much as the change. A key names a model and an object in it;
// when the model is no longer on screen the key is skipped.
//
// A model's key is new with each load, so a state also notes the source each of its models was loaded
// from: when a model has been removed and loaded again, the state's keys are read as the keys the
// model has now. Models loaded and removed are entries too (utils/scene/modelHistory.ts), and what
// such an entry keeps of a model (what was hidden and selected in it) is read and put back here.
import {bindCoreShortcuts} from "@/layout/shortcuts";
import {useScopeStore} from "@/state/scopeStore";
import {diffSets, useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {recordViewerAction} from "@/utils/scene/viewerHistory";
import {recordObjectColors} from "@/viewport/api/objectColors";
import type {ViewportApi} from "@/viewport/api/viewportApi";
import {historyShortcutHandlers} from "@/viewport/api/viewportShortcuts";
import type {ViewportState, ViewportStateChange, ViewportUndoState} from "@/viewport/api/viewportState";
import {setViewportSourceViews, type SourceViews} from "@/viewport/api/sourceViews";
import type {RecordedSourceView} from "@/utils/scene/sceneRecord";
import {objectKey, splitObjectKey, type NativeScene} from "./nativeScene";

const NO_KEYS: readonly string[] = [];

class NativeState implements ViewportState {
    /** What the step between this state and its other end hid and showed; empty until measured. */
    hid: readonly string[] = NO_KEYS;
    showed: readonly string[] = NO_KEYS;

    constructor(
        readonly scene: NativeScene,
        readonly end: "before" | "after",
        /** The selected keys at the time; null when the selection was not asked for. */
        readonly selection: ReadonlySet<string> | null,
        /** The hidden keys at the time, when visibility was asked for (the "before" end only). */
        readonly hiddenThen: ReadonlySet<string> | null,
        /** The source each model on screen at the time was loaded from, by the model's key. */
        readonly sources: ReadonlyMap<string, string>,
    ) {}

    get selected(): number {
        return this.selection?.size ?? 0;
    }
}

function sameKeys(a: ReadonlySet<string>, b: ReadonlySet<string>): boolean {
    if (a.size !== b.size) return false;
    for (const key of a) if (!b.has(key)) return false;
    return true;
}

/** The models of `sources` that have since been removed and loaded again: the key each had then to
 *  the key it has now. Null when every one is on screen as it was, or gone. */
function rekeyed(scene: NativeScene, sources: ReadonlyMap<string, string>): Map<string, string> | null {
    if (!sources.size) return null;
    const now = scene.modelSources();
    let out: Map<string, string> | null = null;
    for (const [key, source] of sources) {
        if (now.has(key)) continue;
        for (const [keyNow, sourceNow] of now) {
            if (sourceNow !== source) continue;
            (out ??= new Map()).set(key, keyNow);
            break;
        }
    }
    return out;
}

/** `keys` with the model of each read through `moved`. */
function* withModelsNow(keys: Iterable<string>, moved: ReadonlyMap<string, string>): Generator<string> {
    for (const key of keys) {
        const {model, id} = splitObjectKey(key);
        const now = moved.get(model);
        yield now === undefined ? key : objectKey(now, id);
    }
}

/** The scene's state for the history. */
export function nativeUndoState(scene: NativeScene): ViewportUndoState {
    const own = (state: ViewportState): NativeState | null =>
        state instanceof NativeState && state.scene === scene ? state : null;
    return {
        captureState: (what) =>
            new NativeState(scene, "before", what.selection ? new Set(scene.selectedKeys) : null, what.visibility ? new Set(scene.hiddenKeys) : null, scene.modelSources()),
        stateChange: (state): ViewportStateChange => {
            const before = own(state);
            if (!before) throw new Error("a state of another viewport");
            const moved = before.hiddenThen ? diffSets(before.hiddenThen, scene.hiddenKeys) : null;
            const hid = moved?.added ?? NO_KEYS;
            const showed = moved?.removed ?? NO_KEYS;
            return {
                selection: before.selection !== null && !sameKeys(before.selection, scene.selectedKeys),
                visibility: hid.length + showed.length > 0,
                hidden: hid.length,
                shown: showed.length,
                after: () => {
                    const after = new NativeState(scene, "after", before.selection ? new Set(scene.selectedKeys) : null, null, before.sources);
                    before.hid = after.hid = hid;
                    before.showed = after.showed = showed;
                    return after;
                },
            };
        },
        restoreState: (state) => {
            const end = own(state);
            if (!end) return;
            let {selection, hid, showed} = end;
            // A model removed and loaded again since has new keys: the state is read by its source.
            const moved = rekeyed(scene, end.sources);
            if (moved) {
                selection = selection && new Set(withModelsNow(selection, moved));
                hid = [...withModelsNow(hid, moved)];
                showed = [...withModelsNow(showed, moved)];
            }
            // Back to "before" shows what the step hid and hides what it showed; on to "after" the reverse.
            if (end.end === "before") scene.restoreSets(selection, showed, hid);
            else scene.restoreSets(selection, hid, showed);
        },
    };
}

type Recorded = "setSelection" | "clearSelection" | "hide" | "show" | "isolate" | "showAll" | "hideSelected" | "hideOthers" | "setObjectColors";

/** `api` with the state for the history, and with each call that changes the selection or what is
 *  hidden recorded as one undo step. A click in the view is one too. */
export function withNativeHistory(scene: NativeScene, api: Omit<ViewportApi, keyof ViewportUndoState>): ViewportApi {
    scene.pickStep = (picked, change) => recordViewerAction(picked ? "select" : "clear-selection", change);
    const steps: Pick<ViewportApi, Recorded> = {
        // Selecting nothing is labelled as clearing the selection, whichever kind it is recorded as.
        setSelection: (refs, options) => recordViewerAction("select", () => api.setSelection(refs, options), {coalesce: options?.coalesce}),
        clearSelection: () => recordViewerAction("clear-selection", () => api.clearSelection()),
        hide: (refs) => recordViewerAction("hide", () => api.hide(refs)),
        show: (refs) => recordViewerAction("show", () => api.show(refs)),
        isolate: (refs) => recordViewerAction("isolate", () => api.isolate(refs)),
        showAll: () => recordViewerAction("show-all", () => api.showAll()),
        hideSelected: () => recordViewerAction("hide", () => api.hideSelected()),
        hideOthers: () => recordViewerAction("hide-others", () => api.hideOthers()),
        setObjectColors: (refs, color) => recordObjectColors(color === null, () => api.setObjectColors(refs, color)),
    };
    return {...api, ...steps, ...nativeUndoState(scene)};
}

/** The ids of model `model`'s objects among `keys`. */
function idsOf(keys: ReadonlySet<string>, model: string): string[] {
    const prefix = objectKey(model, "");
    const out: string[] = [];
    for (const key of keys) if (key.startsWith(prefix)) out.push(key.slice(prefix.length));
    return out;
}

/** The ids of model `model`'s objects that are drawn now, when they are fewer than `limit`; null
 *  when they are not (the search stops there). */
function shownIds(scene: NativeScene, model: string, limit: number): string[] | null {
    const prefix = objectKey(model, "");
    const out: string[] = [];
    for (const object of scene.objects) {
        if (!object.id.startsWith(prefix) || scene.isHidden(object.id)) continue;
        if (out.length + 1 >= limit) return null;
        out.push(object.id.slice(prefix.length));
    }
    return out;
}

/** What is hidden and selected in model `model`: under an isolation the few objects still drawn
 *  instead of the many hidden, as the scene record keeps a view. */
function viewOf(scene: NativeScene, model: string): RecordedSourceView | null {
    const hidden = scene.hiddenKeys.size ? idsOf(scene.hiddenKeys, model) : [];
    const selected = scene.selectedKeys.size ? idsOf(scene.selectedKeys, model) : [];
    if (!hidden.length && !selected.length) return null;
    const shown = hidden.length ? shownIds(scene, model, hidden.length) : null;
    return {...(shown ? {shown} : hidden.length ? {hidden} : {}), ...(selected.length ? {selected} : {})};
}

/** The keys `view` hides in the model that has key `model` now. */
function hiddenKeysOf(scene: NativeScene, model: string, view: RecordedSourceView): string[] {
    if (!view.shown) return view.hidden?.map((id) => objectKey(model, id)) ?? [];
    const prefix = objectKey(model, "");
    const shown = new Set(view.shown);
    const out: string[] = [];
    for (const object of scene.objects) {
        if (object.id.startsWith(prefix) && !shown.has(object.id.slice(prefix.length))) out.push(object.id);
    }
    return out;
}

function keyOfSource(scene: NativeScene, sourceName: string): string | null {
    for (const [key, source] of scene.modelSources()) if (source === sourceName) return key;
    return null;
}

/** How long a model that is being loaded again is waited for before its view is given up. */
const LOADED_AGAIN_TIMEOUT_MS = 5000;

/** Resolves with the key of the model loaded from `sourceName` once it is on screen, or null. */
function whenOnScreen(scene: NativeScene, sourceName: string): Promise<string | null> {
    const key = keyOfSource(scene, sourceName);
    if (key !== null) return Promise.resolve(key);
    return new Promise((resolve) => {
        const done = (found: string | null) => {
            clearTimeout(timer);
            scene.onLoaded.delete(look);
            resolve(found);
        };
        const look = () => {
            const found = keyOfSource(scene, sourceName);
            if (found !== null) done(found);
        };
        const timer = setTimeout(() => done(null), LOADED_AGAIN_TIMEOUT_MS);
        scene.onLoaded.add(look);
    });
}

/** What is hidden and selected per model of the scene, for the history's entries of models loaded
 *  and removed. `release` stops it listening to the scene. */
export function nativeSourceViews(scene: NativeScene): SourceViews & {release: () => void} {
    // A load that replaces the scene empties it before anyone is told which models went: what
    // they looked like is noted as they go, until the removal's entry asks.
    let replaced = new Map<string, RecordedSourceView>();
    const note = () => {
        replaced = new Map();
        if (!scene.hiddenKeys.size && !scene.selectedKeys.size) return;
        for (const [key, source] of scene.modelSources()) {
            const view = viewOf(scene, key);
            if (view) replaced.set(source, view);
        }
    };
    scene.onReplacing.add(note);
    return {
        capture: (sourceName) => {
            const key = keyOfSource(scene, sourceName);
            if (key !== null) return viewOf(scene, key);
            const view = replaced.get(sourceName) ?? null;
            replaced.delete(sourceName);
            return view;
        },
        apply: async (sourceName, view) => {
            const key = await whenOnScreen(scene, sourceName);
            if (key === null) return;
            const hide = hiddenKeysOf(scene, key, view);
            let selected: Set<string> | null = null;
            if (view.selected?.length) {
                // Added to the selection: other models keep theirs.
                selected = new Set(scene.selectedKeys);
                for (const id of view.selected) selected.add(objectKey(key, id));
            }
            scene.restoreSets(selected, hide, []);
        },
        release: () => void scene.onReplacing.delete(note),
    };
}

/** What ties the history to this viewport while it is up: the Undo and Redo keys, what the entries
 *  of models loaded and removed keep of a model, and forgetting the history when its entries no
 *  longer name what is on screen (another scope, the viewport gone). A model that replaces the
 *  scene keeps the history: its entries name models by their source, and work again once an undo
 *  has brought the model back. Returns the unbinding. */
export function bindNativeHistory(scene: NativeScene): () => void {
    const reset = () => useViewerHistoryStore.getState().reset();
    const unbindKeys = bindCoreShortcuts(historyShortcutHandlers((run) => ({run})));
    const offScope = useScopeStore.subscribe((s, prev) => {
        if (s.current?.kind !== prev.current?.kind || s.current?.id !== prev.current?.id) reset();
    });
    const views = nativeSourceViews(scene);
    const withdrawViews = setViewportSourceViews(views);
    return () => {
        withdrawViews();
        views.release();
        offScope();
        unbindKeys();
        reset();
    };
}
