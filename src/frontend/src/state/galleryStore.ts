import {create} from "zustand";
import {persist} from "zustand/middleware";

// Gallery mode: a small prev/next HUD that cycles the current scope's
// loadable files one at a time (clear → load), showing the active
// file's storage path. Toggled from the Theme options submenu. The
// enabled flag persists (a viewing preference); the index is transient
// and lives in the GalleryControls component (it depends on the current
// scope's file list, which changes per scope / refresh).
interface GalleryState {
    enabled: boolean;
    setEnabled: (v: boolean) => void;
    toggle: () => void;
}

export const useGalleryStore = create<GalleryState>()(
    persist(
        (set) => ({
            enabled: false,
            setEnabled: (v) => set({enabled: v}),
            toggle: () => set((s) => ({enabled: !s.enabled})),
        }),
        {name: "ada-gallery"},
    ),
);
