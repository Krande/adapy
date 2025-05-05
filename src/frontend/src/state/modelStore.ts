// modelStore.ts
import {create} from 'zustand';
import * as THREE from 'three';
import {SceneOperations} from "../flatbuffers/scene/scene-operations";
import {FilePurpose} from "../flatbuffers/base/file-purpose";

export interface ModelState {
    modelUrl: string | null;
    scene_action: SceneOperations | null;
    scene_action_arg: string | null;
    model_type: FilePurpose | null;
    userdata: any;
    translation: THREE.Vector3 | null;
    boundingBox: THREE.Box3 | null;
    raycaster: THREE.Raycaster | null;
    zIsUp: boolean;
    defaultOrbitController: boolean;

    // Functions to set the state
    setModelUrl: (
        url: string | null,
        scene_action: SceneOperations | null,
        scene_action_arg: string | null,
        model_type: FilePurpose
    ) => void;
    setTranslation: (translation: THREE.Vector3) => void;
    setBoundingBox: (boundingBox: THREE.Box3) => void;
    setUserData: (userdata: any) => void;
    setRaycaster: (raycaster: THREE.Raycaster | null) => void;
    setZIsUp: (zIsUp: boolean) => void;
    setDefaultOrbitController: (OrbitController: boolean) => void;
}

export const useModelStore = create<ModelState>((set) => ({
    modelUrl: null,
    scene_action: null,
    scene_action_arg: null,
    userdata: null,
    translation: null,
    boundingBox: null,
    raycaster: null,
    zIsUp: true, // default to Z being up
    defaultOrbitController: true,
    model_type: null,

    setModelUrl: (url, scene_action, scene_action_arg, model_type) =>
        set({
            modelUrl: url,
            scene_action: scene_action,
            scene_action_arg: scene_action_arg,
            model_type: model_type

        }),
    setTranslation: (translation) => set({translation}),
    setBoundingBox: (boundingBox) => set({boundingBox}),
    setUserData: (userdata) => set({userdata}),
    setRaycaster: (raycaster: THREE.Raycaster | null) => set({raycaster}),
    setZIsUp: (zIsUp) => set({zIsUp}),
    setDefaultOrbitController: (OrbitController) => set({defaultOrbitController: OrbitController}),
}));
