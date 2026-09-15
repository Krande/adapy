import {create} from "zustand";
import {overlay_file_in_scene} from "@/utils/scene/handlers/overlay_file_in_scene";
import {load_fea_with_defaults} from "@/utils/scene/handlers/load_fea_streaming";
import {canLoadIntoSceneLegacy, isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {scopeUrlPart, useScopeStore} from "@/state/scopeStore";

// Sequential scene-load queue. overlay_file_in_scene shares loader
// state, so concurrent loads corrupt the scene — but that's an
// implementation constraint, not a UX one: the user can keep ticking
// checkboxes and the queue drains one model at a time. The unified
// ConversionProgress toast renders current + queued + errors from this
// store (as the "Loading" continuation of a model's convert→load
// lifecycle); the storage rows mark queued entries and let an un-tick remove them
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

// Offer the file to whichever plugin claimed its kind. Dynamically imported for
// the same reason `context.ts` dynamically imports the model loader: the run
// point pulls in the plugin context, which reaches the mounted viewer's runtime
// and its loaders, and this store is on the boot path.
async function openWithPlugin(name: string): Promise<boolean> {
    const {openRenderableFile} = await import("@/plugins/renderableFiles");
    // The scope is resolved HERE and not asked of the plugin: a key belongs to
    // the scope it was listed in, and letting a provider name a different one
    // would let it read blobs from a scope the user is not browsing.
    return openRenderableFile(name, scopeUrlPart(useScopeStore.getState().current));
}

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
        } else if (canLoadIntoSceneLegacy(next.name)) {
            await overlay_file_in_scene(next.name);
        } else if (await openWithPlugin(next.name)) {
            // A plugin registered a renderable-file provider for this kind of
            // key and has put the model in the scene (plugin API 1.5.0).
            //
            // TRIED AFTER BOTH CORE PATHS, NEVER BEFORE THEM: installing a
            // plugin must not silently change how an .ifc loads, so a provider
            // is reached only for a file core itself cannot open.
        } else {
            // Neither core path claims it and no plugin does either. Fall
            // through to the overlay anyway, exactly as this branch always did,
            // so an enqueue from OUTSIDE the UI — a deep link, a caller that
            // never consulted `canOpenInScene` — still fails with the convert
            // error it has always failed with rather than with silence.
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
