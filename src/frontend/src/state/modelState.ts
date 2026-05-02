// modelStore.ts
import {create} from 'zustand';
import * as THREE from 'three';
import {SceneOperations} from "../flatbuffers/scene/scene-operations";
import {FilePurpose} from "../flatbuffers/base/file-purpose";

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

// Source-name → THREE.Group lookup. Lives outside the zustand store
// because we'd rather not pay the immutability penalty for a Map of
// scene refs that's only mutated from the load/unload code paths.
// The store carries the *names* (so React re-renders the storage
// list checkboxes) and this map carries the actual group pointers
// for scene removal.
export const loadedSourceGroups: Map<string, THREE.Group> = new Map();

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
        // file as active. Group refs are dropped too — the previous
        // models are about to leave the scene anyway.
        loadedSourceGroups.clear();
        set({
            loadedSourceName: name,
            loadedSourceNames: name === null ? new Set<string>() : new Set([name]),
        });
    },
    registerLoadedSource: (name, group) => {
        loadedSourceGroups.set(name, group);
        set((s) => {
            const next = new Set(s.loadedSourceNames);
            next.add(name);
            return {loadedSourceNames: next, loadedSourceName: name};
        });
    },
    unregisterLoadedSource: (name) => {
        const group = loadedSourceGroups.get(name) ?? null;
        loadedSourceGroups.delete(name);
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
        loadedSourceGroups.clear();
        set({loadedSourceNames: new Set<string>(), loadedSourceName: null});
    },
}));
