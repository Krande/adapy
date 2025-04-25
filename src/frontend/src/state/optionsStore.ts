import { create } from "zustand";
import { webSocketHandler } from "../utils/websocket_connector";
import { useWebSocketStore } from "./webSocketStore";
import { handleWebSocketMessage } from "../utils/handleWebSocketMessage";

export type OptionsState = {
  isOptionsVisible: boolean;
  showPerf: boolean;
  showEdges: boolean;
  lockTranslation: boolean;
  enableWebsocket: boolean;

  setIsOptionsVisible: (value: boolean) => void;
  setShowPerf: (value: boolean) => void;
  setShowEdges: (value: boolean) => void;
  setLockTranslation: (value: boolean) => void;
  setEnableWebsocket: (value: boolean) => void;
};

export const useOptionsStore = create<OptionsState>((set) => ({
  isOptionsVisible: false,
  showPerf: false,
  showEdges: true,
  lockTranslation: false,
  enableWebsocket: true,

  setIsOptionsVisible: (isVisible) => set({ isOptionsVisible: isVisible }),
  setShowPerf: (show) => set({ showPerf: show }),
  setShowEdges: (show) => set({ showEdges: show }),
  setLockTranslation: (lock) => set({ lockTranslation: lock }),

  setEnableWebsocket: (enable) => {
    if (enable) {
      const url = useWebSocketStore.getState().webSocketAddress;
      webSocketHandler.connect(url, handleWebSocketMessage());
    } else {
      webSocketHandler.disconnect();
    }
    set({ enableWebsocket: enable });
  },
}));
