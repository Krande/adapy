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
}

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
    setLoadedSourceName: (name) => set({loadedSourceName: name}),
}));
