// The 3D view's projection: perspective (the default) or orthographic. One setting for the
// page, remembered in this browser through the store's `persist` middleware. The three.js
// viewport follows it (utils/scene/orthographicProjection.ts), and so does the three.js-free one,
// with the adacpp renderer's own camera (viewport/native/nativeScene.ts). A viewport without an
// orthographic view says so (`ViewportApi.unavailable("orthographic")`), and the command is then
// offered disabled. No three.js here, so that chunk may import it.

import {create} from "zustand";
import {persist} from "zustand/middleware";

/** The localStorage entry the setting is kept under. */
export const PROJECTION_STORAGE_KEY = "ada:camera-projection";

interface ProjectionState {
    orthographic: boolean;
    setOrthographic(on: boolean): void;
    toggleOrthographic(): void;
}

export const useProjectionStore = create<ProjectionState>()(
    persist(
        (set, get) => ({
            orthographic: false,
            setOrthographic: (on) => set({orthographic: on}),
            toggleOrthographic: () => set({orthographic: !get().orthographic}),
        }),
        {
            name: PROJECTION_STORAGE_KEY,
            version: 1,
            partialize: (s) => ({orthographic: s.orthographic}),
        },
    ),
);

/**
 * Call `apply` with the projection now, and again each time it changes; returns the
 * unsubscribe. How a viewport keeps its camera in step with the setting.
 */
export function followProjection(apply: (orthographic: boolean) => void): () => void {
    apply(useProjectionStore.getState().orthographic);
    return useProjectionStore.subscribe((now, before) => {
        if (now.orthographic !== before.orthographic) apply(now.orthographic);
    });
}
