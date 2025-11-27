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
    showInfoBox: boolean;

    setConnected: (connected: boolean) => void;
    setFrontendId: (id: number) => void;
    setProcessInfo: (info: ServerProcessInfo | null) => void;
    setConnectedClients: (clients: WebClientInfo[]) => void;
    setLogFilePath: (path: string | null) => void;
    setShowInfoBox: (show: boolean) => void;
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
