import {create} from "zustand";

/** Which number labels core draws over the 3D view (`layout/NumberLabels`). Per session, not persisted. */
export interface LabelsState {
    nodes: boolean;
    elements: boolean;
    setNodes: (v: boolean) => void;
    setElements: (v: boolean) => void;
}

export const useLabelsStore = create<LabelsState>((set) => ({
    nodes: false,
    elements: false,
    setNodes: (nodes) => set({nodes}),
    setElements: (elements) => set({elements}),
}));
