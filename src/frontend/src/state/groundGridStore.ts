// Whether the 3D view draws the ground grid (viewport/api/groundGrid): shown by default, the
// user's choice remembered in this browser. Both viewports follow it. No three.js here.

import {create} from "zustand";

const KEY = "ada:ground-grid";

const read = (): boolean => {
    try {
        return window.localStorage.getItem(KEY) !== "off";
    } catch {
        return true;
    }
};

interface GroundGridState {
    visible: boolean;
    setVisible(on: boolean): void;
    toggle(): void;
}

export const useGroundGridStore = create<GroundGridState>((set, get) => ({
    visible: typeof window === "undefined" ? true : read(),
    setVisible: (on) => {
        try {
            if (on) window.localStorage.removeItem(KEY);
            else window.localStorage.setItem(KEY, "off");
        } catch {
            // Private window or blocked storage: the choice lasts this page.
        }
        set({visible: on});
    },
    toggle: () => get().setVisible(!get().visible),
}));
