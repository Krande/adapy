import {create} from "zustand";
import {persist} from "zustand/middleware";

// Whether the models loaded before a reload are loaded again after it, and how far the restore
// running now has got. The record itself is in utils/scene/sceneRecord. `enabled` is persisted to
// localStorage like the other Options-panel preferences; the progress is not.

export interface SceneRestoreProgress {
    /** Models the restore set out to load. */
    total: number;
    /** Models attempted so far. */
    done: number;
    /** Models that could not be loaded, and were dropped from the record. */
    failed: number;
    running: boolean;
}

interface SceneRestoreState {
    enabled: boolean;
    progress: SceneRestoreProgress | null;
    setEnabled: (v: boolean) => void;
    setProgress: (p: SceneRestoreProgress | null) => void;
}

export const useSceneRestoreStore = create<SceneRestoreState>()(
    persist(
        (set) => ({
            enabled: true,
            progress: null,
            setEnabled: (enabled) => set({enabled}),
            setProgress: (progress) => set({progress}),
        }),
        {
            name: "ada-scene-restore",
            partialize: (s) => ({enabled: s.enabled}),
            merge: (persisted, current) => {
                const p = (persisted ?? {}) as Partial<SceneRestoreState>;
                return {...current, enabled: typeof p.enabled === "boolean" ? p.enabled : current.enabled};
            },
        },
    ),
);
