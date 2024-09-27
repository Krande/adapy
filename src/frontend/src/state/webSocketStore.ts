import {create} from 'zustand';

type WebSocketStore = {
    webSocketAddress: string;
    sendData: (data: string) => void;
    setWebSocketAddress: (address: string) => void;
};

export const useWebSocketStore = create<WebSocketStore>((set) => {
    const websocketPort = (window as any).WEBSOCKET_PORT || 8765;
    const webSocketAddress = 'ws://localhost:' + websocketPort;

    return {
        webSocketAddress,
        sendData: (data: string) => {},
        setWebSocketAddress: (address) => set({webSocketAddress: address}),
    };
});