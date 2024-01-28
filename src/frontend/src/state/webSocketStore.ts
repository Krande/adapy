import {create} from 'zustand';

type WebSocketStore = {
    webSocketAddress: string;
    sendData: (data: string) => void;
    setWebSocketAddress: (address: string) => void;
};

export const useWebSocketStore = create<WebSocketStore>((set) => {
    const webSocketAddress = 'ws://localhost:8765';

    return {
        webSocketAddress,
        sendData: (data: string) => {},
        setWebSocketAddress: (address) => set({webSocketAddress: address}),
    };
});