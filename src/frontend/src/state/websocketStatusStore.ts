import {create} from 'zustand';

export interface WebClientInfo {
    instanceId: number;
    name: string | null;
    address: string | null;
    port: number;
    lastHeartbeat: bigint;
}

export interface ServerProcessInfo {
    pid: number;
    threadId: number;
    logFilePath: string | null;
}

interface WebsocketStatusStore {
    connected: boolean;
    frontendId: number;
    processInfo: ServerProcessInfo | null;
    connectedClients: WebClientInfo[];
    logFilePath: string | null;
    /** @deprecated The connection panel's flag. Ask `isPanelOpen("core:ws-status")` (`@/viewer-core`); open and close it with `reveal` / `hide`. */
    showInfoBox: boolean;

    setConnected: (connected: boolean) => void;
    setFrontendId: (id: number) => void;
    setProcessInfo: (info: ServerProcessInfo | null) => void;
    setConnectedClients: (clients: WebClientInfo[]) => void;
    setLogFilePath: (path: string | null) => void;
    /** @deprecated Use `reveal("core:ws-status")` / `hide("core:ws-status")` from `@/viewer-core`, which the layout controls. */
    setShowInfoBox: (show: boolean) => void;
    /** @deprecated Use `toggle("core:ws-status")` from `@/viewer-core`, which the layout controls. */
    toggleShowInfoBox: () => void;
}

export const useWebsocketStatusStore = create<WebsocketStatusStore>((set) => ({
    connected: false,
    frontendId: 0,
    processInfo: null,
    connectedClients: [],
    logFilePath: null,
    showInfoBox: false,

    setConnected: (connected) => set({connected}),
    setFrontendId: (frontendId) => set({frontendId}),
    setProcessInfo: (processInfo) => set({processInfo}),
    setConnectedClients: (connectedClients) => set({connectedClients}),
    setLogFilePath: (logFilePath) => set({logFilePath}),
    setShowInfoBox: (show) => set({showInfoBox: show}),
    toggleShowInfoBox: () => set((s) => ({showInfoBox: !s.showInfoBox})),
}));
