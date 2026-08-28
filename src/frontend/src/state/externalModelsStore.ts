import {create} from "zustand";

// Visibility for the external-models panel in the menu bar.
//
// Deliberately NOT persisted. The panel's contents depend on the current
// scope's binding, which an admin can change or remove at any time; restoring
// it open across reloads would reopen a panel that may now have nothing behind
// it. The button is one click.

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
