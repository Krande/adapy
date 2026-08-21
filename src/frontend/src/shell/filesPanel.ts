import {create} from "zustand";
import {persist} from "zustand/middleware";

// The Files flyout: a column of its own between the rail and the left dock.
//
// Not a dock panel, and not a mode.
//
// Not a mode ("Library"), because browsing files is not an activity you switch into — it
// is something you do briefly, in the middle of another activity, to open the thing you
// are about to work on. Making it a mode meant leaving whatever you were doing to go and
// find a file.
//
// Not a dock TAB either, because sharing the left dock with the Outliner means opening
// Files hides the model tree you were reading. They answer different questions — "what
// exists on the server" and "what is in this scene" — and you often want both. Its own
// track lets it push rather than replace, and the canvas reflows as it always does.
//
// This is the activity-bar pattern: a strip of icons that reveals a panel beside itself,
// which is what PyCharm's tool windows and VS Code's sidebar both do.

const MIN_W = 220;
const MAX_W = 560;
export const DEFAULT_FILES_W = 300;

export const clampFilesWidth = (w: number) => Math.max(MIN_W, Math.min(MAX_W, Math.round(w)));

interface FilesPanelState {
    shown: boolean;
    width: number;
    setShown: (shown: boolean) => void;
    toggle: () => void;
    setWidth: (w: number) => void;
}

export const useFilesPanel = create<FilesPanelState>()(
    persist(
        (set, get) => ({
            // Closed by default. It is the thing you reach for at the start of a task,
            // not something that should be occupying a column while you model.
            shown: false,
            width: DEFAULT_FILES_W,
            setShown: (shown) => set({shown}),
            toggle: () => set({shown: !get().shown}),
            setWidth: (w) => set({width: clampFilesWidth(w)}),
        }),
        {name: "ada:files-panel:v1"},
    ),
);

export const toggleFilesPanel = () => useFilesPanel.getState().toggle();
export const filesPanelShown = () => useFilesPanel.getState().shown;
