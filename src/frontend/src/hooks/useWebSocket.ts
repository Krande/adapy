import {useCallback, useEffect, useRef} from 'react';

const useWebSocket = (url: string, onMessageReceived: (event: MessageEvent) => void) => {
    const socketRef = useRef<WebSocket | null>(null);

    useEffect(() => {
        const socket = new WebSocket(url);
        socketRef.current = socket;
        console.log('WebSocket connecting to:', url);
        socket.addEventListener('message', (event: MessageEvent) => {
            if (onMessageReceived) {
                onMessageReceived(event);
            }
        });
        socket.addEventListener('error', (error: Event) => {
            console.error('WebSocket error:', error);
        });

        return () => {
            socket.close();
        };
    }, [url, onMessageReceived]);

    return useCallback((data: string) => {
        const currentSocket = socketRef.current;
        if (currentSocket && currentSocket.readyState === WebSocket.OPEN) {
            currentSocket.send(JSON.stringify(data));
        }
    }, []);
};

export default useWebSocket;