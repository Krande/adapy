// websocket_connector_async.ts
export class AsyncWebSocketHandler {
    public socket: WebSocket | null = null;
    public retryWait = 1000;
    public instance_id = this.getRandomInt32();
    private shouldReconnect = true;

    private getRandomInt32(): number {
        return Math.floor(
            Math.random() * (2147483647 - -2147483648 + 1)
        ) + -2147483648;
    }

    async connect(url: string): Promise<void> {
        const overrideId = (window as any).WEBSOCKET_ID;
        if (overrideId) {
            console.log('WebSocket ID Override:', overrideId);
            this.instance_id = overrideId;
        }

        const wsUrl = `${url}?client-type=web&instance-id=${this.instance_id}`;
        this.shouldReconnect = true;

        try {
            this.socket = new WebSocket(wsUrl);
            console.log('WebSocket connecting to:', wsUrl);

            // Set up handlers *before* waiting for open()
            this.socket.addEventListener('message', evt => this.enqueue(evt));
            this.socket.addEventListener('close', () => {
                console.log('WebSocket connection closed.');
                if (this.shouldReconnect) {
                    console.log(
                        `Retrying in ${this.retryWait / 1000}s…`
                    );
                    setTimeout(() => this.connect(url), this.retryWait);
                }
            });
            this.socket.addEventListener('error', err => {
                // console.error('WebSocket raw error:', err);
                // let the open()/error promise below handle scheduling
            });

            // Wait for open *or* immediate error
            await new Promise<void>((resolve, reject) => {
                this.socket!.addEventListener('open', () => {
                    console.log('WebSocket connected');
                    resolve();
                });
                this.socket!.addEventListener('error', ev => {
                    reject(ev);
                });
            });
        } catch (err) {
            // console.error('WebSocket connection failed:', err);
        }
    }

    private messageQueue: MessageEvent[] = [];
    private resolvers: ((evt: MessageEvent) => void)[] = [];
    private enqueue(evt: MessageEvent) {
        if (this.resolvers.length) {
            this.resolvers.shift()!(evt);
        } else {
            this.messageQueue.push(evt);
        }
    }

    nextMessage(): Promise<MessageEvent> {
        return new Promise(resolve => {
            if (this.messageQueue.length) {
                resolve(this.messageQueue.shift()!);
            } else {
                this.resolvers.push(resolve);
            }
        });
    }

    async *messages(): AsyncGenerator<MessageEvent> {
        while (true) {
            yield await this.nextMessage();
        }
    }

    async sendMessage(
        data: string | object | Uint8Array
    ): Promise<void> {
        if (this.socket?.readyState !== WebSocket.OPEN) {
            throw new Error('WebSocket is not open');
        }
        if (typeof data === 'string' || data instanceof Uint8Array) {
            this.socket.send(data);
        } else {
            this.socket.send(JSON.stringify(data));
        }
    }

    async disconnect(): Promise<void> {
        this.shouldReconnect = false;
        this.resolvers.forEach(r =>
            r(new MessageEvent('close'))
        );
        this.resolvers = [];
        this.messageQueue = [];

        if (this.socket) {
            this.socket.close();
            this.socket = null;
        }
    }
}

export const webSocketAsyncHandler = new AsyncWebSocketHandler();
