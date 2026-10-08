import {create} from "zustand";

// DEPRECATED. Visibility for the external-models panel, which is gone -- the
// tree view's Sources tab browses and loads every provider's models now. Kept
// only because `@/viewer-core/scene` exports it (1.1.0) and a shell built
// against that must still resolve it; nothing in core reads it.

interface ExternalModelsState {
    visible: boolean;
    setVisible: (v: boolean) => void;
    toggle: () => void;
}

export const useExternalModelsStore = create<ExternalModelsState>()((set) => ({
    visible: false,
    setVisible: (v) => set({visible: v}),
    toggle: () => set((s) => ({visible: !s.visible})),
}));
