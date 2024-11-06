import { create } from 'zustand';
import { CustomBatchedMesh } from '../utils/mesh_select/CustomBatchedMesh';

type SelectedObjectState = {
  selectedObjects: Map<CustomBatchedMesh, Set<string>>;
  addSelectedObject: (mesh: CustomBatchedMesh, drawRangeId: string) => void;
  removeSelectedObject: (mesh: CustomBatchedMesh, drawRangeId: string) => void;
  clearSelectedObjects: () => void;
};

export const useSelectedObjectStore = create<SelectedObjectState>((set) => ({
  selectedObjects: new Map(),
  addSelectedObject: (mesh, drawRangeId) =>
    set((state) => {
      const newMap = new Map(state.selectedObjects);
      const existingSet = newMap.get(mesh) || new Set<string>();
      existingSet.add(drawRangeId);
      newMap.set(mesh, existingSet);
      return { selectedObjects: newMap };
    }),
  removeSelectedObject: (mesh, drawRangeId) =>
    set((state) => {
      const newMap = new Map(state.selectedObjects);
      const existingSet = newMap.get(mesh);
      if (existingSet) {
        existingSet.delete(drawRangeId);
        if (existingSet.size === 0) {
          newMap.delete(mesh);
        } else {
          newMap.set(mesh, existingSet);
        }
      }
      return { selectedObjects: newMap };
    }),
  clearSelectedObjects: () => set({ selectedObjects: new Map() }),
}));
