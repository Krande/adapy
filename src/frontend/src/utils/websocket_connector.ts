class WebSocketHandler {
    socket: WebSocket | null;
    retry_wait: number = 1000; // Time to wait before retrying connection in milliseconds
    // Generate a random 32-bit integer for the instance ID

    instance_id: number = this.getRandomInt32();

    constructor() {
        this.socket = null;
    }
    getRandomInt32(): number {
        return Math.floor(Math.random() * (2147483647 - (-2147483648) + 1)) + (-2147483648);
    }
    connect(url: string, onMessageReceived: (event: MessageEvent) => void) {
        // Access the WebSocket ID from the global window object
        const websocketId = (window as any).WEBSOCKET_ID;
        if (websocketId) {
            console.log('WebSocket ID Override:', websocketId);
            this.instance_id = websocketId;
        }
        const clientType = "web";

        // Append instance ID and client type as query parameters
        const wsUrl = `${url}?client-type=${clientType}&instance-id=${this.instance_id}`;

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

    sendMessage(data: string | object | Uint8Array) {
        if (this.socket?.readyState === WebSocket.OPEN) {
            if (typeof data === 'string') {
                // Send string data directly
                this.socket.send(data);
            } else if (data instanceof Uint8Array) {
                // Send binary data directly
                this.socket.send(data);
            } else {
                // Send objects as JSON strings
                this.socket.send(JSON.stringify(data));
            }
        }
    }

    disconnect() {
        if (this.socket) {
            this.socket.close();
        }
    }
}

export const webSocketHandler = new WebSocketHandler();