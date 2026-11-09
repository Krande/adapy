import {create} from "zustand";

/** Which number labels core draws over the 3D view (`layout/NumberLabels`). Per session, not persisted. */
export interface LabelsState {
    nodes: boolean;
    elements: boolean;
    /** Label each element with its value of a property or result field instead of its number. */
    values: boolean;
    /** The field the value labels are of (its canonical name); null = the field the result shows. */
    valueField: string | null;
    setNodes: (v: boolean) => void;
    setElements: (v: boolean) => void;
    setValues: (v: boolean) => void;
    setValueField: (name: string | null) => void;
}

export const useLabelsStore = create<LabelsState>((set) => ({
    nodes: false,
    elements: false,
    values: false,
    valueField: null,
    setNodes: (nodes) => set({nodes}),
    setElements: (elements) => set({elements}),
    setValues: (values) => set({values}),
    setValueField: (valueField) => set({valueField}),
}));
