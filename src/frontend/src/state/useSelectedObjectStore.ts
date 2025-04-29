import { create } from 'zustand';
import { CustomBatchedMesh } from '../utils/mesh_select/CustomBatchedMesh';

type SelectedObjectState = {
  selectedObjects: Map<CustomBatchedMesh, Set<string>>;
  addSelectedObject: (mesh: CustomBatchedMesh, drawRangeId: string) => void;
  removeSelectedObject: (mesh: CustomBatchedMesh, drawRangeId: string) => void;
  clearSelectedObjects: () => void;
  addBatchofMeshes: (batch: [CustomBatchedMesh, string][]) => void;
};

export const useSelectedObjectStore = create<SelectedObjectState>((set) => ({
  selectedObjects: new Map(),

  addSelectedObject: (mesh, drawRangeId) =>
    set((state) => {
      // Clone the map and update the set for this mesh
      const newMap = new Map(state.selectedObjects);
      const existingSet = new Set(newMap.get(mesh) || []);
      existingSet.add(drawRangeId);
      newMap.set(mesh, existingSet);

      // Update mesh grouping to highlight selected ranges
      mesh.updateSelectionGroups(Array.from(existingSet));

      return { selectedObjects: newMap };
    }),

  removeSelectedObject: (mesh, drawRangeId) =>
    set((state) => {
      const newMap = new Map(state.selectedObjects);
      const existingSet = newMap.get(mesh);
      if (existingSet) {
        const newSet = new Set(existingSet);
        newSet.delete(drawRangeId);

        if (newSet.size === 0) {
          // Clear all selection groups when none left
          mesh.clearSelectionGroups();
          newMap.delete(mesh);
        } else {
          newMap.set(mesh, newSet);
          mesh.updateSelectionGroups(Array.from(newSet));
        }
      }
      return { selectedObjects: newMap };
    }),

  clearSelectedObjects: () =>
    set((state) => {
      // Clear selection on all meshes
      state.selectedObjects.forEach((_, mesh) => {
        mesh.clearSelectionGroups();
      });
      return { selectedObjects: new Map() };
    }),

  addBatchofMeshes: (batch) =>
    set((state) => {
      const newMap = new Map(state.selectedObjects);
      const drawRangeGroups = new Map<CustomBatchedMesh, Set<string>>();

      // Group ranges by mesh
      batch.forEach(([mesh, drawRangeId]) => {
        if (!drawRangeGroups.has(mesh)) {
          drawRangeGroups.set(mesh, new Set());
        }
        drawRangeGroups.get(mesh)!.add(drawRangeId);
      });

      // Update each mesh
      drawRangeGroups.forEach((drawRanges, mesh) => {
        const existingSet = new Set(newMap.get(mesh) || []);
        drawRanges.forEach((id) => existingSet.add(id));
        newMap.set(mesh, existingSet);
        mesh.updateSelectionGroups(Array.from(existingSet));
      });

      return { selectedObjects: newMap };
    }),
}));
