import {create} from 'zustand';
import {CustomBatchedMesh} from '../utils/mesh_select/CustomBatchedMesh';

type SelectedObjectState = {
    selectedObjects: Map<CustomBatchedMesh, Set<string>>;
    addSelectedObject: (mesh: CustomBatchedMesh, drawRangeId: string) => void;
    removeSelectedObject: (mesh: CustomBatchedMesh, drawRangeId: string) => void;
    clearSelectedObjects: () => void;
};

export const useSelectedObjectStore = create<SelectedObjectState>(
    (set) => ({
        selectedObjects: new Map(),
        addSelectedObject: (mesh, drawRangeId) =>
            set((state) => {
                // Clone the entire Map
                const newMap = new Map(state.selectedObjects);

                // Clone the Set for the specific mesh, if it exists; otherwise, create a new Set
                const existingSet = new Set(newMap.get(mesh) || []);
                existingSet.add(drawRangeId);

                // Set the updated Set back in the Map
                newMap.set(mesh, existingSet);
                mesh.highlightDrawRanges(Array.from(existingSet || []));
                return {selectedObjects: newMap};
            }),
        removeSelectedObject: (mesh, drawRangeId) =>
            set((state) => {
                const newMap = new Map(state.selectedObjects);

                // Clone the Set for the specific mesh if it exists
                const existingSet = newMap.get(mesh);
                if (existingSet) {
                    const newSet = new Set(existingSet); // Clone the Set to avoid mutating original state
                    newSet.delete(drawRangeId);

                    if (newSet.size === 0) {
                        mesh.deselect();
                        newMap.delete(mesh); // Remove the entry if the Set is empty
                    } else {
                        newMap.set(mesh, newSet); // Update with the modified Set
                        mesh.highlightDrawRanges(Array.from(newSet || []));
                    }

                }
                return {selectedObjects: newMap};
            }),
        clearSelectedObjects: () =>
            set((state) => {
                state.selectedObjects.forEach((_, mesh) => {
                    mesh.deselect();
                });
                return { selectedObjects: new Map() };
            }),
    }));
