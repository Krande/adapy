// src/hooks/useWebSocket.js
import { useEffect, useRef, useCallback } from 'react';

const useWebSocket = (url, onMessageReceived) => {
    const socketRef = useRef(null);

    useEffect(() => {
        socketRef.current = new WebSocket(url);

        socketRef.current.addEventListener('message', (event) => {
            if (onMessageReceived) {
                onMessageReceived(event);
            }
        });

        socketRef.current.addEventListener('error', (error) => {
            console.error('WebSocket error:', error);
        });

        return () => {
            if (socketRef.current) {
                socketRef.current.close();
            }
        };
    }, [url, onMessageReceived]);

    const sendData = useCallback((data) => {
        if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
            socketRef.current.send(data);
        }
    }, []);

    return sendData;
};

export default useWebSocket;
