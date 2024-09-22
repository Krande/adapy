export function sendMessage() {
    console.log('Sending message');
}

class WebSocketHandler {
    socket: WebSocket | null;
    retry_wait: number = 1000; // Time to wait before retrying connection in milliseconds

    constructor() {
        this.socket = null;
    }

    connect(url: string, onMessageReceived: (event: MessageEvent) => void) {
        // Generate a random 32-bit integer for the instance ID
        const instanceId = Math.floor(Math.random() * 0xFFFFFFFF);
        const clientType = "web";

        // Append instance ID and client type as query parameters
        const wsUrl = `${url}?client-type=${clientType}&instance-id=${instanceId}`;

        this.socket = new WebSocket(wsUrl);
        console.log('WebSocket connecting to:', wsUrl);

        this.socket.addEventListener('open', () => {
            console.log('WebSocket connected');
        });

        this.socket.addEventListener('message', (event) => {
            if (onMessageReceived) {
                onMessageReceived(event);
            }
        });

        this.socket.addEventListener('error', (error) => {
            console.error('WebSocket error:', error);
        });

        this.socket.addEventListener('close', (event) => {
            console.log('WebSocket connection closed. Retrying in', this.retry_wait / 1000, 'seconds');
            setTimeout(() => this.connect(url, onMessageReceived), this.retry_wait);
        });
    }

    sendMessage(data: string | object) {
        if (this.socket?.readyState === WebSocket.OPEN) {
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