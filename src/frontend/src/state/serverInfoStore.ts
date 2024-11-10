import {create} from 'zustand';

type ServerInfoState = {
    showServerInfoBox: boolean;
    setShowServerInfoBox: (show_info_box: boolean) => void;
};

export const useServerInfoStore = create<ServerInfoState>((set) => ({
    showServerInfoBox: false,
    setShowServerInfoBox: (show_info_box) => set({showServerInfoBox: show_info_box}),
}));