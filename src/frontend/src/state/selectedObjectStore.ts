import {create} from 'zustand';
import * as THREE from 'three';

type SelectedObjectState = {
    selectedObject: THREE.Mesh | null;
    setSelectedObject: (mesh: THREE.Mesh | null) => void;
    originalMaterial: THREE.MeshBasicMaterial | null;
    setOriginalMaterial: (material: THREE.MeshBasicMaterial | null) => void;
};

export const useSelectedObjectStore = create<SelectedObjectState>((set) => ({
    selectedObject: null,
    setSelectedObject: (mesh) => set(() => ({selectedObject: mesh})),
    originalMaterial: null,
    setOriginalMaterial: (material: THREE.MeshBasicMaterial | null) => set(() => ({originalMaterial: material})),
}));