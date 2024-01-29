import {useCallback, useEffect, useRef} from 'react';

type WebSocketConnection = {
    socket: WebSocket;
    sendMessage: (data: string | object) => void;
} | null;
const establishWebSocketConnection = (url: string, onMessageReceived: (event: MessageEvent) => void) => {
    const socket = new WebSocket(url);
    console.log('WebSocket connecting to:', url);
    socket.addEventListener('message', (event: MessageEvent) => {
        if (onMessageReceived) {
            onMessageReceived(event);
        }
    });
    socket.addEventListener('error', (error: Event) => {
        console.error('WebSocket error:', error);
    });

    const sendMessage = (data: string | object) => {
        if (socket.readyState === WebSocket.OPEN) {
            socket.send(typeof data === 'string' ? data : JSON.stringify(data));
        }
    };

    return {socket, sendMessage};
};

const useWebSocket = (url: string, onMessageReceived: (event: MessageEvent) => void) => {
    const connection = useRef<WebSocketConnection>(null);

    if (!connection.current) {
        connection.current = establishWebSocketConnection(url, onMessageReceived);
    }

    useEffect(() => {
        return () => {
            if (connection.current?.socket) {
                connection.current.socket.close();
            }
        };
    }, []);

    return connection.current;
};


export {useWebSocket, establishWebSocketConnection};