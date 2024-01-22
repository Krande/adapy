import create from 'zustand';
import * as THREE from 'three';

type SelectedObjectState = {
    selectedObject: THREE.Mesh | null;
    setSelectedObject: (mesh: THREE.Mesh | null) => void;
    originalColor: THREE.Color | null;
    setOriginalColor: (color: THREE.Color) => void;
};

export const useSelectedObjectStore = create<SelectedObjectState>((set) => ({
    selectedObject: null,
    setSelectedObject: (mesh) => set(() => ({selectedObject: mesh})),
    originalColor: null,
    setOriginalColor: (color: THREE.Color) => set(() => ({originalColor: color})),
}));