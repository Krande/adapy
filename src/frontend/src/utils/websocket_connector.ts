class WebSocketHandler {
    socket: WebSocket | null = null;
    retry_wait: number = 1000;
    instance_id: number = this.getRandomInt32();
    shouldReconnect: boolean = true;
    reconnectTimeoutId: number | null = null;

    constructor() {}

    getRandomInt32(): number {
        return Math.floor(Math.random() * (2147483647 - (-2147483648) + 1)) + (-2147483648);
    }

    connect(url: string, onMessageReceived: (event: MessageEvent) => void) {
        const websocketId = (window as any).WEBSOCKET_ID;
        if (websocketId) {
            console.log('WebSocket ID Override:', websocketId);
            this.instance_id = websocketId;
        }

        const clientType = "web";
        const wsUrl = `${url}?client-type=${clientType}&instance-id=${this.instance_id}`;

        this.shouldReconnect = true; // Always allow reconnect on fresh connect
        this.socket = new WebSocket(wsUrl);
        console.log('WebSocket connecting to:', wsUrl);

        this.socket.addEventListener('open', () => {
            console.log('WebSocket connected');
        });

        this.socket.addEventListener('message', (event) => {
            onMessageReceived?.(event);
        });

        this.socket.addEventListener('error', (error) => {
            console.error('WebSocket error:', error);
        });

        this.socket.addEventListener('close', () => {
            console.log('WebSocket connection closed.');
            if (this.shouldReconnect) {
                console.log(`Retrying in ${this.retry_wait / 1000} seconds...`);
                this.reconnectTimeoutId = window.setTimeout(() => this.connect(url, onMessageReceived), this.retry_wait);
            }
        });
    }

    sendMessage(data: string | object | Uint8Array) {
        if (this.socket?.readyState === WebSocket.OPEN) {
            if (typeof data === 'string') {
                this.socket.send(data);
            } else if (data instanceof Uint8Array) {
                this.socket.send(data);
            } else {
                this.socket.send(JSON.stringify(data));
            }
        }
    }

    disconnect() {
        this.shouldReconnect = false;

        // Clear any scheduled reconnect
        if (this.reconnectTimeoutId) {
            clearTimeout(this.reconnectTimeoutId);
            this.reconnectTimeoutId = null;
        }

        // Close the socket
        if (this.socket) {
            this.socket.close();
            this.socket = null;
        }
    }
}

export const webSocketSyncHandler = new WebSocketHandler();