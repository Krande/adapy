// modelStore.ts
import {create} from 'zustand';
import * as THREE from 'three';
import {SceneOperations} from "../flatbuffers/scene/scene-operations";
import {FilePurpose} from "../flatbuffers/base/file-purpose";
import {useLineageStore} from './lineageStore';
import {loadedSourceGroups, useModelSessionStore} from './modelSession';

export interface ModelState {
    modelUrl: string | null;
    scene_action: SceneOperations | null;
    model_type: FilePurpose | null;
    userdata: any;
    translation: THREE.Vector3 | null;
    boundingBox: THREE.Box3 | null;
    zIsUp: boolean;
    defaultOrbitController: boolean;
    // Source key (storage filename) currently rendered in the viewer.
    // Tracked separately from `modelUrl` because that one's a transient
    // blob: URL and the storage browser needs the durable name to
    // render its "loaded" marker.
    loadedSourceName: string | null;
    // All source keys currently overlaid in the scene. The set
    // grows as the user toggles checkboxes in StorageBrowser; the
    // group references live on `loadedSourceGroups` (kept off the
    // store to avoid stuffing THREE.Object3D refs into zustand).
    loadedSourceNames: ReadonlySet<string>;

    // Functions to set the state
    setModelUrl: (
        url: string | null,
        scene_action: SceneOperations | null,
    ) => void;
    setTranslation: (translation: THREE.Vector3) => void;
    setBoundingBox: (boundingBox: THREE.Box3) => void;
    setUserData: (userdata: any) => void;
    setZIsUp: (zIsUp: boolean) => void;
    setDefaultOrbitController: (OrbitController: boolean) => void;
    setLoadedSourceName: (name: string | null) => void;
    // Multi-model overlay tracking
    registerLoadedSource: (name: string, group: THREE.Group) => void;
    unregisterLoadedSource: (name: string) => THREE.Group | null;
    clearLoadedSources: () => void;
}

// Source-name → THREE.Group lookup. The map itself belongs to the open
// ``ModelSession`` now (state/modelSession) — so a closed session drops the
// group pointers along with the rest of the model's identity, instead of
// leaving them for the next teardown path to remember. Still exported from
// here, and still outside the zustand store, because the reasons for both are
// unchanged: eight modules import it by this name, and nothing re-renders off
// THREE.Object3D pointers. The store carries the *names* (so React re-renders
// the storage list checkboxes) and the session carries the pointers.
export { loadedSourceGroups } from './modelSession';

export const useModelState = create<ModelState>((set) => ({
    modelUrl: null,
    scene_action: null,
    userdata: null,
    translation: null,
    boundingBox: null,
    zIsUp: true, // default to Z being up
    defaultOrbitController: true,
    model_type: null,
    loadedSourceName: null,
    loadedSourceNames: new Set<string>(),

    setModelUrl: (url, scene_action) =>
        set({
            modelUrl: url,
            scene_action: scene_action,
        }),
    setTranslation: (translation) => set({translation}),
    setBoundingBox: (boundingBox) => set({boundingBox}),
    setUserData: (userdata) => set({userdata}),
    setZIsUp: (zIsUp) => set({zIsUp}),
    setDefaultOrbitController: (OrbitController) => set({defaultOrbitController: OrbitController}),
    setLoadedSourceName: (name) => {
        // Replace semantic: this is what the legacy single-model
        // load path (view_file_object_from_server / replace_model)
        // calls on success. Wipes any prior overlay set so the
        // storage list checkboxes only show the single just-loaded
        // file as active. Opening a session is what drops the group
        // refs — the previous models are about to leave the scene
        // anyway — and it is the point at which the viewer knows
        // which model it is showing.
        if (name === null) {
            useModelSessionStore.getState().close();
        } else {
            useModelSessionStore.getState().open({sourceName: name});
        }
        set({
            loadedSourceName: name,
            loadedSourceNames: name === null ? new Set<string>() : new Set([name]),
        });
    },
    registerLoadedSource: (name, group) => {
        // Opens a session when there is none: the websocket REPLACE path
        // registers its group without naming the model first, and an overlay
        // can be the first thing in an empty scene.
        const session = useModelSessionStore.getState().ensure();
        session.groups.set(name, group);
        session.identity.sourceName ??= name;
        // Trigger lineage registration if the loader stashed extension
        // data on this group. Dynamic import avoids a require cycle
        // (lineage helpers also reach into modelState for the active
        // file). Fire-and-forget; the lineage map is non-critical so a
        // failure here mustn't block the source registration.
        const ext = (group as any)?.children?.[0]?.userData?.__adaExt
            ?? (group as any)?.userData?.__adaExt;
        const gltf = (group as any)?.children?.[0]?.userData?.__adaGltf
            ?? (group as any)?.userData?.__adaGltf;
        if (ext && gltf) {
            void import('@/utils/lineage/registerLineageFromExtension').then(({registerLineageFromExtension}) =>
                registerLineageFromExtension({gltf, extension: ext, fileName: name, root: group}),
            ).catch((err) => console.warn('lineage: register failed for', name, err))
                .finally(() => {
                    // Lineage was the last consumer of the GLTFLoader parser. Dropping
                    // the stashed reference releases the parser's caches INCLUDING the
                    // raw GLB body ArrayBuffer (the geometry attributes own their own
                    // copies) — on a multi-hundred-MB model this is the difference
                    // between the binary staying resident forever and being GC'd.
                    const holders = [(group as any)?.children?.[0]?.userData, (group as any)?.userData];
                    for (const ud of holders) {
                        if (ud && '__adaGltf' in ud) delete ud.__adaGltf;
                    }
                });
            // Build the reverse member→connection index so the
            // selection inspector can show "Connections (N)" for a
            // clicked beam/plate without re-reading the GLB.
            // Synchronous + cheap (small dict walk); a future model
            // load replaces it.
            void import('@/state/connectionGraphStore').then(({buildConnectionIndex, useConnectionGraphStore}) => {
                useConnectionGraphStore.getState().setIndex(buildConnectionIndex(ext));
            }).catch((err) => console.warn('connection graph: build failed for', name, err));
        }
        set((s) => {
            const next = new Set(s.loadedSourceNames);
            next.add(name);
            return {loadedSourceNames: next, loadedSourceName: name};
        });
    },
    unregisterLoadedSource: (name) => {
        const group = loadedSourceGroups.get(name) ?? null;
        loadedSourceGroups.delete(name);
        // Drop this file's lineage entries so a future click in another
        // file doesn't try to jump to a model that's no longer in the
        // scene. Symmetric to register-on-load in setupModelLoaderAsync.
        useLineageStore.getState().unregister(name);
        set((s) => {
            if (!s.loadedSourceNames.has(name)) return {};
            const next = new Set(s.loadedSourceNames);
            next.delete(name);
            // If the just-removed name was the highlighted one, fall
            // back to whatever's still loaded (Set iteration order
            // is insertion order in modern JS).
            const newHighlight =
                s.loadedSourceName === name
                    ? (next.size ? Array.from(next).pop() ?? null : null)
                    : s.loadedSourceName;
            return {loadedSourceNames: next, loadedSourceName: newHighlight};
        });
        return group;
    },
    clearLoadedSources: () => {
        // Ends the session outright: both callers (replace_model and
        // clear_loaded_model) are tearing the scene down, and both go on to
        // call clearActiveFeaStreaming, which closes it too. Closing here as
        // well keeps the group refs from outliving the model by those few
        // lines.
        useModelSessionStore.getState().close();
        useLineageStore.getState().clear();
        set({loadedSourceNames: new Set<string>(), loadedSourceName: null});
    },
}));
