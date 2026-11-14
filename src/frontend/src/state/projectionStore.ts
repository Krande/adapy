// The 3D view's projection: perspective (the default) or orthographic. One setting for the
// page, remembered in this browser; the three.js viewport follows it
// (utils/scene/orthographicProjection.ts). No three.js here: the three.js-free viewport's
// chunk may read it.

import {create} from "zustand";

const KEY = "ada:camera-projection";

const read = (): boolean => {
    try {
        return window.localStorage.getItem(KEY) === "orthographic";
    } catch {
        return false;
    }
};

interface ProjectionState {
    orthographic: boolean;
    setOrthographic(on: boolean): void;
    toggle(): void;
}

export const useProjectionStore = create<ProjectionState>((set, get) => ({
    orthographic: typeof window === "undefined" ? false : read(),
    setOrthographic: (on) => {
        try {
            if (on) window.localStorage.setItem(KEY, "orthographic");
            else window.localStorage.removeItem(KEY);
        } catch {
            // Private window or blocked storage: the choice lasts this page.
        }
        set({orthographic: on});
    },
    toggle: () => get().setOrthographic(!get().orthographic),
}));
