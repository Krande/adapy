// useOptionsStore.ts
import {create} from "zustand";
import {webSocketAsyncHandler} from "../utils/websocket/websocket_connector_async";
import {useWebSocketStore} from "./webSocketStore";
import {handleWebSocketMessage} from "../utils/websocket/handleWebSocketMessage";

export type OptionsState = {
    isOptionsVisible: boolean;
    showPerf: boolean;
    showEdges: boolean;
    lockTranslation: boolean;
    enableWebsocket: boolean;
    enableNodeEditor: boolean;
    pointSize: number;
    pointSizeAbsolute: boolean;
    useGpuPointPicking: boolean;

    setIsOptionsVisible: (value: boolean) => void;
    setShowPerf: (value: boolean) => void;
    setShowEdges: (value: boolean) => void;
    setLockTranslation: (value: boolean) => void;
    setEnableWebsocket: (value: boolean) => void; // we’ll make this async internally
    setEnableNodeEditor: (value: boolean) => void;
    setPointSize: (value: number) => void;
    setPointSizeAbsolute: (value: boolean) => void;
    setUseGpuPointPicking: (value: boolean) => void;
};

export const useOptionsStore = create<OptionsState>((set) => ({
    isOptionsVisible: false,
    showPerf: false,
    showEdges: true,
    lockTranslation: false,
    enableWebsocket: true,
    enableNodeEditor: false,
    pointSize: 0.01,
    pointSizeAbsolute: true,
    useGpuPointPicking: false,

    setIsOptionsVisible: (v) => set({isOptionsVisible: v}),
    setShowPerf: (v) => set({showPerf: v}),
    setShowEdges: (v) => set({showEdges: v}),
    setLockTranslation: (v) => set({lockTranslation: v}),
    setEnableNodeEditor: (v) => set({enableNodeEditor: v}),
    setPointSize: (v) => set({pointSize: v}),
    setPointSizeAbsolute: (v) => set({pointSizeAbsolute: v}),
    setUseGpuPointPicking: (v) => set({useGpuPointPicking: v}),

    // toggle WS on/off
    setEnableWebsocket: async (enable: boolean) => {
        // 1) flip the UI state right away
        set({enableWebsocket: enable});

        try {
            if (enable) {
                const url = useWebSocketStore.getState().webSocketAddress;
                await webSocketAsyncHandler.connect(url);

                (async () => {
                    for await (const evt of webSocketAsyncHandler.messages()) {
                        await handleWebSocketMessage(evt);
                    }
                })().catch(err => {
                    console.error("Error in WS message loop:", err);
                });
            } else {
                // gracefully tear down
                await webSocketAsyncHandler.disconnect();
            }
        } catch (err) {
            console.error("Error toggling WebSocket:", err);
        }
    },
}));
