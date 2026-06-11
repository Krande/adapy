import {create} from "zustand";
import {overlay_file_in_scene} from "@/utils/scene/handlers/overlay_file_in_scene";
import {load_fea_with_defaults} from "@/utils/scene/handlers/load_fea_streaming";
import {isStreamingFEAResult} from "@/utils/scene/fileKinds";

// Sequential scene-load queue. overlay_file_in_scene shares loader
// state, so concurrent loads corrupt the scene — but that's an
// implementation constraint, not a UX one: the user can keep ticking
// checkboxes and the queue drains one model at a time. The toast
// (LoadQueueToast) renders current + queued + errors from this store;
// the storage rows mark queued entries and let an un-tick remove them
// before their turn comes.

export interface LoadTask {
    name: string;
    /** Memory-bounded streaming STEP→GLB path (row menu action). */
    streamer?: boolean;
}

interface LoadQueueState {
    current: LoadTask | null;
    queued: LoadTask[];
    errors: Array<{name: string; message: string}>;
    enqueue: (task: LoadTask) => void;
    removeQueued: (name: string) => void;
    clearError: (name: string) => void;
}

export const useLoadQueueStore = create<LoadQueueState>((set, get) => ({
    current: null,
    queued: [],
    errors: [],
    enqueue: (task) => {
        const s = get();
        if (s.current?.name === task.name) return;
        if (s.queued.some((t) => t.name === task.name)) return;
        set({
            queued: [...s.queued, task],
            // Re-queuing a previously failed load clears its stale error.
            errors: s.errors.filter((e) => e.name !== task.name),
        });
        void runNext();
    },
    removeQueued: (name) =>
        set((s) => ({queued: s.queued.filter((t) => t.name !== name)})),
    clearError: (name) =>
        set((s) => ({errors: s.errors.filter((e) => e.name !== name)})),
}));

async function runNext(): Promise<void> {
    const store = useLoadQueueStore;
    if (store.getState().current) return;
    const next = store.getState().queued[0];
    if (!next) return;
    store.setState((s) => ({current: next, queued: s.queued.slice(1)}));
    try {
        if (next.streamer) {
            await overlay_file_in_scene(next.name, undefined, {streamer: true});
        } else if (isStreamingFEAResult(next.name)) {
            // Streaming-FEA replaces the whole scene (replace_model) —
            // documented behavior of loading such a file; queued
            // overlays after it land on the fresh scene.
            await load_fea_with_defaults(next.name);
        } else {
            await overlay_file_in_scene(next.name);
        }
    } catch (err) {
        console.error("queued load failed", next.name, err);
        store.setState((s) => ({
            errors: [
                ...s.errors,
                {name: next.name, message: err instanceof Error ? err.message : String(err)},
            ],
        }));
    } finally {
        store.setState({current: null});
        void runNext();
    }
}
