// The saved scene states of the current scope, as the Scene panel lists them: save what the scene
// shows now, put a saved one back, delete one. Every change goes through `changeSceneStates` (read
// the latest document, apply, write), so states others saved since are kept.
//
// Putting one back is the one restore path (`utils/scene/applySceneState`), the same a detail
// model's "Load" uses.

import {create} from "zustand";

import type {ApplyOutcome} from "@/utils/scene/applySceneState";
import type {SceneRecord} from "@/utils/scene/sceneRecord";
import {
    newSceneStateId,
    type SavedSceneState,
    type SceneStatesChange,
    type SceneStatesDoc,
} from "@/utils/scene/savedSceneStates";
import {sceneRecordEmpty} from "@/utils/scene/sceneRecord";

/** The I/O the store does, swappable for its test. */
export interface SavedSceneStatesDeps {
    read(scope: string): Promise<SceneStatesDoc>;
    change(scope: string, change: SceneStatesChange): Promise<SceneStatesDoc>;
    /** What the scene shows now, and how many loaded models cannot be kept in a state. */
    capture(): Promise<{state: SceneRecord; leftOut: number}>;
    apply(scope: string, state: SceneRecord): Promise<ApplyOutcome>;
    now(): Date;
}

const realDeps: SavedSceneStatesDeps = {
    async read(scope) {
        const {readSceneStatesDoc} = await import("@/services/savedSceneStates");
        return readSceneStatesDoc(scope);
    },
    async change(scope, change) {
        const {changeSceneStates} = await import("@/services/savedSceneStates");
        return changeSceneStates(scope, change);
    },
    async capture() {
        const {captureSceneState} = await import("@/utils/scene/sceneStateLive");
        return captureSceneState();
    },
    async apply(scope, state) {
        const {applySceneStateLive} = await import("@/utils/scene/sceneStateLive");
        return applySceneStateLive(scope, state);
    },
    now: () => new Date(),
};

interface SavedSceneStatesState {
    /** The scope `states` belong to; null until loaded. */
    scope: string | null;
    states: readonly SavedSceneState[];
    /** The state being saved, restored or deleted (`"new"` while saving). */
    busyId: string | null;
    /** Why the list could not be read or written. */
    error: string | null;
    /** What the last save or restore could not keep or put back, in sentences. */
    notes: readonly string[];
    deps: SavedSceneStatesDeps;
    load: (scope: string) => Promise<void>;
    /** Save what the scene shows now under `name`. The saved state, or null (`error`/`notes` say why). */
    save: (scope: string, name: string, createdBy?: string | null) => Promise<SavedSceneState | null>;
    restore: (scope: string, id: string) => Promise<void>;
    remove: (scope: string, id: string) => Promise<void>;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export const useSavedSceneStatesStore = create<SavedSceneStatesState>((set, get) => ({
    scope: null,
    states: [],
    busyId: null,
    error: null,
    notes: [],
    deps: realDeps,

    load: async (scope) => {
        try {
            const doc = await get().deps.read(scope);
            set({scope, states: doc.states, error: null});
        } catch (e) {
            set({scope, states: [], error: `The saved scene states could not be read: ${message(e)}`});
        }
    },

    save: async (scope, name, createdBy) => {
        const trimmed = name.trim();
        if (!trimmed || get().busyId) return null;
        set({busyId: "new", error: null, notes: []});
        try {
            const {state, leftOut} = await get().deps.capture();
            if (sceneRecordEmpty(state)) {
                set({busyId: null, notes: ["Nothing in the scene can be loaded again from a saved state, so nothing was saved."]});
                return null;
            }
            const saved: SavedSceneState = {
                id: newSceneStateId(),
                name: trimmed,
                created_at: get().deps.now().toISOString(),
                ...(createdBy ? {created_by: createdBy} : {}),
                state,
            };
            const doc = await get().deps.change(scope, {kind: "put", state: saved});
            const notes = leftOut
                ? [`${leftOut} model${leftOut === 1 ? " is" : "s are"} not stored in this scope (opened from disk, or made in this page) and ${leftOut === 1 ? "was" : "were"} left out.`]
                : [];
            set({scope, states: doc.states, busyId: null, notes});
            return saved;
        } catch (e) {
            set({busyId: null, error: `The scene state could not be saved: ${message(e)}`});
            return null;
        }
    },

    restore: async (scope, id) => {
        const saved = get().states.find((s) => s.id === id);
        if (!saved || get().busyId) return;
        set({busyId: id, error: null, notes: []});
        try {
            const out = await get().deps.apply(scope, saved.state);
            const notes = [...out.notes, ...(out.detailError ? [`The detail model could not be loaded: ${out.detailError}`] : [])];
            set({busyId: null, notes});
        } catch (e) {
            set({busyId: null, error: `"${saved.name}" could not be restored: ${message(e)}`});
        }
    },

    remove: async (scope, id) => {
        if (get().busyId) return;
        set({busyId: id, error: null});
        try {
            const doc = await get().deps.change(scope, {kind: "delete", id});
            set({scope, states: doc.states, busyId: null});
        } catch (e) {
            set({busyId: null, error: `The scene state could not be deleted: ${message(e)}`});
        }
    },
}));
