import {create} from "zustand";

// One number that moves whenever any mesh hides or shows a draw range. The hidden sets themselves
// live on the meshes (CustomBatchedMesh) and change in place, so nothing React-side could see them
// change; whatever draws visibility (the Scene tree's rows) subscribes to this and re-reads.

interface VisibilityState {
    version: number;
    bump: () => void;
}

export const useVisibilityStore = create<VisibilityState>()((set) => ({
    version: 0,
    bump: () => set((s) => ({version: s.version + 1})),
}));
