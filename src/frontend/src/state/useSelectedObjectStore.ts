import {create} from 'zustand';
import {CustomBatchedMesh} from '../utils/mesh_select/CustomBatchedMesh';
import * as THREE from "three";
import {Object3D} from "three";
import {clearPointSelectionMask} from "../utils/scene/pointsImpostor";

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
            // if the mesh is an instance of CustomBatchedMesh then do this
            if (!(mesh instanceof CustomBatchedMesh)) {
                // loop recursively through the children of the mesh
                (mesh as Object3D).traverse((child: Object3D) => {
                    if (child instanceof CustomBatchedMesh) {
                        child.updateSelectionGroups(Array.from(existingSet));
                    }
                });

            } else {
                mesh.updateSelectionGroups(Array.from(existingSet));
            }


            return {selectedObjects: newMap};
        }),

    removeSelectedObject: (mesh, drawRangeId) =>
        set((state) => {
            const newMap = new Map(state.selectedObjects);
            const existingSet = newMap.get(mesh);
            if (existingSet) {
                const newSet = new Set(existingSet);
                newSet.delete(drawRangeId);

                if (newSet.size === 0) {
                    // When no ranges left, clear mesh/points highlight appropriately
                    if (mesh instanceof CustomBatchedMesh) {
                        mesh.clearSelectionGroups();
                    } else if ((mesh as any).isPoints) {
                        clearPointSelectionMask(mesh as unknown as THREE.Points);
                    } else {
                        // loop recursively through the children of the mesh
                        (mesh as Object3D).traverse((child: Object3D) => {
                            if (child instanceof CustomBatchedMesh) {
                                child.clearSelectionGroups();
                            }
                        });
                    }
                    newMap.delete(mesh);
                } else {
                    newMap.set(mesh, newSet);
                    // Update only CustomBatchedMesh selection groups here, points are updated by click handlers
                    if (mesh instanceof CustomBatchedMesh) {
                        mesh.updateSelectionGroups(Array.from(newSet));
                    } else if (!(mesh as any).isPoints) {
                        (mesh as Object3D).traverse((child: Object3D) => {
                            if (child instanceof CustomBatchedMesh) {
                                child.updateSelectionGroups(Array.from(newSet));
                            }
                        });
                    }
                }
            }
            return {selectedObjects: newMap};
        }),

    clearSelectedObjects: () =>
        set((state) => {
            // Clear selection on all stored objects
            state.selectedObjects.forEach((_, mesh) => {
                if (mesh instanceof CustomBatchedMesh) {
                    mesh.clearSelectionGroups();
                } else if ((mesh as any).isPoints) {
                    clearPointSelectionMask(mesh as unknown as THREE.Points);
                } else {
                    // loop recursively through the children of the mesh
                    (mesh as Object3D).traverse((child: Object3D) => {
                        if (child instanceof CustomBatchedMesh) {
                            child.clearSelectionGroups();
                        }
                    });
                }
            });
            // Also clear any single-point overlay highlight (fallback path)
            return {selectedObjects: new Map()};
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
                if (!(mesh instanceof CustomBatchedMesh)) {
                    // loop recursively through the children of the mesh
                    (mesh as Object3D).traverse((child: Object3D) => {
                        if (child instanceof CustomBatchedMesh) {
                            child.updateSelectionGroups(Array.from(existingSet));
                        }
                    });

                } else {
                    mesh.updateSelectionGroups(Array.from(existingSet));
                }
            });

            return {selectedObjects: newMap};
        }),
}));
