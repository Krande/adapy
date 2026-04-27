import {create} from 'zustand';
import {runtime} from '@/runtime/config';

type WebSocketStore = {
    webSocketAddress: string;
    sendData: (data: string) => void;
    setWebSocketAddress: (address: string) => void;
};

export const useWebSocketStore = create<WebSocketStore>((set) => {
    const webSocketAddress = `ws://localhost:${runtime.websocketPort()}`;

    return {
        webSocketAddress,
        sendData: (data: string) => {},
        setWebSocketAddress: (address) => set({webSocketAddress: address}),
    };
});