/**
 * Models loaded into and removed from the scene, as undo / redo entries.
 *
 * One change of the loaded models (`useModelState.loadedSourceNames`) is one entry, whichever path
 * made it -- the Sources tree, the file browser, a drop, a search hit, a requested node, the Scene
 * panel's remove, a plugin.
 *
 *  - Undo of a LOAD removes the model again; its redo loads it again.
 *  - Undo of a REMOVE loads the model again and puts back what was hidden and selected in it; its
 *    redo removes it again.
 *
 * Loading again goes through the same paths a page restore uses (`sceneRestore.ts`): a stored file
 * by its key, a published node by its node ref -- so it shows as a normal load, and no geometry is
 * kept in memory. What a model looked like is the scene record's view of it
 * (`sceneViewRestore.ts`): its hidden elements (or isolation) and its selected ones, by element id.
 * The loader places it as it did the first time; the viewer has no per-model move / rotate.
 *
 * Nothing here knows how a model is drawn: loading, removing and reading or putting back what a
 * model looked like are the caller's (`viewerHistoryBindings.ts`), which asks the viewport on screen.
 *
 * A removal is recorded only for a model that can be loaded again (one the scene record knows
 * how to fetch); a model that cannot, e.g. a local file dropped without uploading, leaves no entry
 * rather than one whose undo would fail. Removing a model from the SCENE is what is undone: deleting
 * a stored file or a published asset on the server is not a viewer operation and is not covered.
 *
 * Such an entry is async: the store holds every other undo / redo off until the load settles.
 */

import {useModelState} from "@/state/modelState";
import {describeModelChange, useViewerHistoryStore, type ViewerHistoryEntry} from "@/state/viewerHistoryStore";

import type {RecordedModel, RecordedSourceView} from "./sceneRecord";

export interface ModelHistoryDeps {
    /** How the model loaded under `name` can be loaded again, or null when it cannot. */
    describe: (name: string) => RecordedModel | null;
    /** Load `model` again, as a normal load; true when it is in the scene afterwards. */
    load: (model: RecordedModel) => Promise<boolean>;
    /** Remove the model loaded under `name` from the scene. */
    unload: (name: string) => void | Promise<void>;
    /** Something of the model loaded under `name` that can still be read once the scene has let go
     *  of it (the three.js viewport's group), handed back to `captureView`; undefined for none. */
    hold: (name: string) => unknown;
    /** What is hidden and selected in the model loaded under `name`, or null when nothing is. */
    captureView: (name: string, held: unknown) => RecordedSourceView | null;
    /** Hide and select again, in the model just loaded under `name`, what `view` names. */
    applyView: (name: string, view: RecordedSourceView) => Promise<void>;
    /** False while loads are not the user's to undo (a page restore, a gallery walk, a builder
     *  compiling its preview). */
    shouldRecord: () => boolean;
}

/** One model as its removal left it: how to load it again and what it looked like. */
interface Removed {
    name: string;
    /** Null when it cannot be loaded again. */
    model: RecordedModel | null;
    view: RecordedSourceView | null;
}

function snapshot(name: string, held: unknown, deps: ModelHistoryDeps): Removed {
    return {name, model: deps.describe(name), view: deps.captureView(name, held)};
}

async function loadAgain(removed: readonly Removed[], deps: ModelHistoryDeps): Promise<void> {
    for (const r of removed) {
        if (useModelState.getState().loadedSourceNames.has(r.name)) continue;
        if (!r.model) {
            console.warn(`viewer history: "${r.name}" cannot be loaded again from where it came`);
            continue;
        }
        const ok = await deps.load(r.model);
        if (!ok) {
            console.warn(`viewer history: "${r.name}" could not be loaded again`);
            continue;
        }
        if (r.view) await deps.applyView(r.name, r.view);
    }
}

async function removeAgain(names: readonly string[], deps: ModelHistoryDeps): Promise<void> {
    for (const name of names) {
        if (useModelState.getState().loadedSourceNames.has(name)) await deps.unload(name);
    }
}

/** The entry for models just loaded under `names`. */
export function loadEntry(names: readonly string[], deps: ModelHistoryDeps): ViewerHistoryEntry {
    // Filled by the undo, which sees the models as the user left them.
    let removed: Removed[] = [];
    return {
        label: describeModelChange("add", names),
        undo: async () => {
            removed = names.map((n) => snapshot(n, deps.hold(n), deps));
            await removeAgain(names, deps);
        },
        redo: () => loadAgain(removed, deps),
    };
}

/** The entry for models just removed, as `removed` captured them. */
export function removeEntry(removed: readonly Removed[], deps: ModelHistoryDeps): ViewerHistoryEntry {
    let state = [...removed];
    const names = removed.map((r) => r.name);
    return {
        label: describeModelChange("remove", names),
        undo: () => loadAgain(state, deps),
        redo: async () => {
            // What the user did to the model since it came back is what the next undo restores.
            state = state.map((r) => {
                const now = snapshot(r.name, deps.hold(r.name), deps);
                return {...now, model: now.model ?? r.model};
            });
            await removeAgain(names, deps);
        },
    };
}

/** Record every load and removal of a model from now on. Returns the unbinding. */
export function bindModelHistory(deps: ModelHistoryDeps): () => void {
    // Removing a model unregisters its group before the scene is torn down, and before the store
    // tells anyone: what was held of each is kept here so a removal can still read what the model
    // looked like.
    const groups = new Map<string, unknown>();
    for (const name of useModelState.getState().loadedSourceNames) {
        const g = deps.hold(name);
        if (g) groups.set(name, g);
    }

    return useModelState.subscribe((s, prev) => {
        if (s.loadedSourceNames === prev.loadedSourceNames) return;
        const added = [...s.loadedSourceNames].filter((n) => !prev.loadedSourceNames.has(n));
        const gone = [...prev.loadedSourceNames].filter((n) => !s.loadedSourceNames.has(n));
        const lost = gone.map((n) => [n, groups.get(n)] as const);
        for (const n of gone) groups.delete(n);
        for (const n of added) {
            const g = deps.hold(n);
            if (g) groups.set(n, g);
        }

        const history = useViewerHistoryStore.getState();
        if (history.applying || !deps.shouldRecord()) return;
        if (gone.length) {
            const removed = lost.map(([n, g]) => snapshot(n, g, deps)).filter((r) => r.model !== null);
            if (removed.length) history.record(removeEntry(removed, deps));
        }
        if (added.length) history.record(loadEntry(added, deps));
    });
}
