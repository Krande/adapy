// modelStore.ts
import {create} from 'zustand';
import * as THREE from 'three';
import {SceneOperations} from "../flatbuffers/wsock/scene-operations";

export interface ModelState {
    modelUrl: string | null;
    scene: THREE.Scene | null;
    scene_action: SceneOperations | null;
    scene_action_arg: string | null;
    userdata: any;
    translation: THREE.Vector3 | null;
    boundingBox: THREE.Box3 | null;
    raycaster: THREE.Raycaster | null;
    zIsUp: boolean;
    defaultOrbitController: boolean;
    should_hide_edges: boolean;

    // Functions to set the state
    setModelUrl: (
        url: string | null,
        scene_action: SceneOperations | null,
        scene_action_arg: string | null
    ) => void;
    setTranslation: (translation: THREE.Vector3) => void;
    setBoundingBox: (boundingBox: THREE.Box3) => void;
    setScene: (scene: THREE.Scene | null) => void;
    setUserData: (userdata: any) => void;
    setRaycaster: (raycaster: THREE.Raycaster | null) => void;
    setZIsUp: (zIsUp: boolean) => void;
    setDefaultOrbitController: (OrbitController: boolean) => void;
    setShouldHideEdges: (should_hide_edges: boolean) => void;
}

export const useModelStore = create<ModelState>((set) => ({
    modelUrl: null,
    scene: null,
    scene_action: null,
    scene_action_arg: null,
    userdata: null,
    translation: null,
    boundingBox: null,
    raycaster: null,
    zIsUp: true, // default to Z being up
    defaultOrbitController: true,
    should_hide_edges: false,

    setShouldHideEdges: (should_hide_edges) => set({should_hide_edges}),
    setModelUrl: (url, scene_action, scene_action_arg) =>
        set({
            modelUrl: url,
            scene_action: scene_action,
            scene_action_arg: scene_action_arg,
        }),
    setTranslation: (translation) => set({translation}),
    setBoundingBox: (boundingBox) => set({boundingBox}),
    setScene: (scene: THREE.Scene | null) => set({scene}),
    setUserData: (userdata) => set({userdata}),
    setRaycaster: (raycaster: THREE.Raycaster | null) => set({raycaster}),
    setZIsUp: (zIsUp) => set({zIsUp}),
    setDefaultOrbitController: (OrbitController) => set({defaultOrbitController: OrbitController}),
}));
