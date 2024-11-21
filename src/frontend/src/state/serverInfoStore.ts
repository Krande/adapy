import {create} from 'zustand';
import {FileObjectT} from "../flatbuffers/wsock/file-object";

type ServerInfoState = {
    showServerInfoBox: boolean;
    setShowServerInfoBox: (show_info_box: boolean) => void;
    serverFiles: string[];
    setServerFiles: (serverFiles: string[]) => void;
};

export const useServerInfoStore = create<ServerInfoState>((set) => ({
    serverFiles: [],
    setServerFiles: (serverFiles) => set({serverFiles: serverFiles}),
    showServerInfoBox: false,
    setShowServerInfoBox: (show_info_box) => set({showServerInfoBox: show_info_box}),
}));