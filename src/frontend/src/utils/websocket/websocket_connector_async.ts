// websocket_connector_async.ts

import { useWebSocketStore } from "../../state/webSocketStore";
import { useWebsocketStatusStore } from "../../state/websocketStatusStore";
import { requestServerInfo } from "./requestServerInfo";
import { requestConnectedClients } from "./requestConnectedClients";

export class AsyncWebSocketHandler {
  public socket: WebSocket | null = null;
  public retryWait = 1000;
  public instance_id = this.getRandomInt32();
  private shouldReconnect = true;
  private lastUrl: string | null = null;

  private getRandomInt32(): number {
    return (
      Math.floor(Math.random() * (2147483647 - -2147483648 + 1)) + -2147483648
    );
  }

  async connect(url: string): Promise<void> {
    const statusStore = useWebsocketStatusStore.getState();

    const overrideId = (window as any).WEBSOCKET_ID;
    if (overrideId) {
      console.log("WebSocket ID Override:", overrideId);
      this.instance_id = overrideId;
    }

    // remember last attempted URL for possible reconnects (e.g., when ID changes)
    this.lastUrl = url;

    const wsUrl = `${url}?client-type=web&instance-id=${this.instance_id}`;
    this.shouldReconnect = true;

    try {
      this.socket = new WebSocket(wsUrl);
      console.log("WebSocket connecting to:", wsUrl);

      // Set up handlers *before* waiting for open()
      this.socket.addEventListener("message", (evt) => this.enqueue(evt));
      this.socket.addEventListener("close", () => {
        console.log("WebSocket connection closed.");
        statusStore.setConnected(false);
        statusStore.setProcessInfo(null);
        statusStore.setConnectedClients([]);
        if (this.shouldReconnect) {
          console.log(`Retrying in ${this.retryWait / 1000}s…`);
          setTimeout(() => this.connect(url), this.retryWait);
        }
      });
      this.socket.addEventListener("error", (err) => {
        // console.error('WebSocket raw error:', err);
        // let the open()/error promise below handle scheduling
      });

      // Wait for open *or* immediate error
      await new Promise<void>((resolve, reject) => {
        this.socket!.addEventListener("open", () => {
          console.log("WebSocket connected");
          // Update connection status
          statusStore.setConnected(true);
          // reflect the current instance id in the status store
          statusStore.setFrontendId(this.instance_id);
          requestServerInfo();
          requestConnectedClients();
          resolve();
        });
        this.socket!.addEventListener("error", (ev) => {
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
    return new Promise((resolve) => {
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

  async sendMessage(data: string | object | Uint8Array): Promise<void> {
    if (this.socket?.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket is not open");
    }
    if (typeof data === "string" || data instanceof Uint8Array) {
      this.socket.send(data);
    } else {
      this.socket.send(JSON.stringify(data));
    }
  }

  async disconnect(): Promise<void> {
    this.shouldReconnect = false;
    this.resolvers.forEach((r) => r(new MessageEvent("close")));
    this.resolvers = [];
    this.messageQueue = [];

    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
  }

  /**
   * Update the client instance id. Optionally triggers a reconnect so the
   * server immediately sees the new id in the connection query string.
   */
  async setInstanceId(newId: number, reconnect: boolean = true): Promise<void> {
    // Coerce to integer and clamp to signed 32-bit range
    if (typeof newId !== "number" || !Number.isFinite(newId)) {
      throw new Error("Instance ID must be a finite number");
    }
    newId = Math.trunc(newId);
    const INT32_MIN = -2147483648;
    const INT32_MAX = 2147483647;
    if (newId < INT32_MIN || newId > INT32_MAX) {
      throw new Error(`Instance ID must be int32 (${INT32_MIN}..${INT32_MAX})`);
    }

    this.instance_id = newId;
    // Persist override so page reload keeps the chosen ID
    (window as any).WEBSOCKET_ID = newId;

    // Update UI store immediately
    const statusStore = useWebsocketStatusStore.getState();
    statusStore.setFrontendId(newId);

    if (reconnect) {
      const url = this.lastUrl;
      if (url) {
        const wasConnected = this.socket?.readyState === WebSocket.OPEN;
        try {
          if (wasConnected || this.socket) {
            await this.disconnect();
          }
        } catch (_) {
          // ignore
        }
        await this.connect(url);
      }
    }
  }
}

export const webSocketAsyncHandler = new AsyncWebSocketHandler();
