// Undo / redo on the three.js-free viewport: its state as the history captures and restores it
// (viewport/api/viewportState.ts), and each of its operations that changes the selection or what is
// hidden made one undo step (utils/scene/viewerHistory.ts), as on the three.js viewport.
//
// The scene keeps two sets of object keys, selected and hidden. A state is the selected keys as a copy
// and, for a step that hid or showed something, the keys it hid and the keys it showed: undo and redo
// move only those, so an entry costs as much as the change. A key names a model and an object in it;
// when the model is no longer on screen the key is skipped.
import {bindCoreShortcuts} from "@/layout/shortcuts";
import {useScopeStore} from "@/state/scopeStore";
import {diffSets, useViewerHistoryStore} from "@/state/viewerHistoryStore";
import {recordViewerAction} from "@/utils/scene/viewerHistory";
import type {ViewportApi} from "@/viewport/api/viewportApi";
import {historyShortcutHandlers} from "@/viewport/api/viewportShortcuts";
import type {ViewportState, ViewportStateChange, ViewportUndoState} from "@/viewport/api/viewportState";
import type {NativeScene} from "./nativeScene";

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

/** The scene's state for the history. */
export function nativeUndoState(scene: NativeScene): ViewportUndoState {
    const own = (state: ViewportState): NativeState | null =>
        state instanceof NativeState && state.scene === scene ? state : null;
    return {
        captureState: (what) =>
            new NativeState(scene, "before", what.selection ? new Set(scene.selectedKeys) : null, what.visibility ? new Set(scene.hiddenKeys) : null),
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
                    const after = new NativeState(scene, "after", before.selection ? new Set(scene.selectedKeys) : null, null);
                    before.hid = after.hid = hid;
                    before.showed = after.showed = showed;
                    return after;
                },
            };
        },
        restoreState: (state) => {
            const end = own(state);
            if (!end) return;
            // Back to "before" shows what the step hid and hides what it showed; on to "after" the reverse.
            if (end.end === "before") scene.restoreSets(end.selection, end.showed, end.hid);
            else scene.restoreSets(end.selection, end.hid, end.showed);
        },
    };
}

type Recorded = "setSelection" | "clearSelection" | "hide" | "show" | "isolate" | "showAll" | "hideSelected" | "hideOthers";

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
    };
    return {...api, ...steps, ...nativeUndoState(scene)};
}

/** What ties the history to this viewport while it is up: the Undo and Redo keys, and forgetting the
 *  history when its entries no longer name what is on screen (another scope, a model that replaced
 *  the scene, the viewport gone). Returns the unbinding. */
export function bindNativeHistory(scene: NativeScene): () => void {
    const reset = () => useViewerHistoryStore.getState().reset();
    const unbindKeys = bindCoreShortcuts(historyShortcutHandlers((run) => ({run})));
    const offScope = useScopeStore.subscribe((s, prev) => {
        if (s.current?.kind !== prev.current?.kind || s.current?.id !== prev.current?.id) reset();
    });
    // A load that joins the models on screen keeps the scene, and with it the history; one that
    // replaces them starts both over.
    let epoch = scene.sceneEpoch;
    const replaced = () => {
        if (scene.sceneEpoch === epoch) return;
        epoch = scene.sceneEpoch;
        reset();
    };
    scene.onLoaded.add(replaced);
    return () => {
        scene.onLoaded.delete(replaced);
        offScope();
        unbindKeys();
        reset();
    };
}
