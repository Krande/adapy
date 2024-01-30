export function sendMessage() {
    console.log('Sending message');
}

class WebSocketHandler {
    constructor() {
        this.socket = null;
    }

    connect(url) {
        this.socket = new WebSocket(url);
        console.log('WebSocket connecting to:', url);
        this.socket.addEventListener('message', (event) => {
            if (this.onMessageReceived) {
                this.onMessageReceived(event);
            }
        });
        this.socket.addEventListener('error', (error) => {
            console.error('WebSocket error:', error);
        });
    }

    sendMessage(data) {
        if (this.socket.readyState === WebSocket.OPEN) {
            this.socket.send(typeof data === 'string' ? data : JSON.stringify(data));
        }
    }

    disconnect() {
        if (this.socket) {
            this.socket.close();
        }
    }
}

export const webSocketHandler = new WebSocketHandler();