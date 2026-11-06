import {create} from 'zustand';
import {FileType} from "../flatbuffers/base";

export interface ServerFileEntry {
    name: string;
    fileType: FileType;
    filepath: string;
    lastModified: string;  // ISO-8601, "" when unavailable
}

type ServerInfoState = {
    /** @deprecated The Storage (hosted) / Server info (desktop) panel's flag. Ask `isPanelOpen("core:storage")` or `"core:server-info"` (`@/viewer-core`); open and close it with `reveal` / `hide`. */
    showServerInfoBox: boolean;
    /** @deprecated Use `reveal` / `hide` of `"core:storage"` (hosted) or `"core:server-info"` (desktop) from `@/viewer-core`, which the layout controls. */
    setShowServerInfoBox: (show_info_box: boolean) => void;
    serverFiles: string[];
    setServerFiles: (serverFiles: string[]) => void;
    serverFileObjects: ServerFileEntry[];
    setServerFileObjects: (files: ServerFileEntry[]) => void;
    showUrdfLoader: boolean;
    setShowUrdfLoader: (showUrdfLoader: boolean) => void;
};

export const useServerInfoStore = create<ServerInfoState>((set) => ({
    serverFiles: [],
    setServerFiles: (serverFiles) => set({serverFiles: serverFiles}),
    serverFileObjects: [],
    setServerFileObjects: (files) => set({serverFileObjects: files}),
    showServerInfoBox: false,
    setShowServerInfoBox: (show_info_box) => set({showServerInfoBox: show_info_box}),
    showUrdfLoader: false,
    setShowUrdfLoader: (showUrdfLoader) => set({showUrdfLoader: showUrdfLoader}),
}));
