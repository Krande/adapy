import { useWebsocketStatusStore } from "@/state/websocketStatusStore";
import { runtime } from "@/runtime/config";
import type { Comms, CommsConnectHandler, CommsMessageHandler, Unsubscribe } from "./types";

const INT32_MAX = 2147483647;
const INT32_MIN = -2147483648;

function getRandomInt32(): number {
  return Math.floor(Math.random() * INT32_MAX) + 1;
}

function normalizeInstanceId(id: number): number {
  if (typeof id !== "number" || !Number.isFinite(id)) return getRandomInt32();
  id = Math.trunc(id);
  if (id === 0) return 1;
  if (id < 0) id = Math.abs(id);
  if (id > INT32_MAX) id = INT32_MAX;
  return id;
}

export class WSComms implements Comms {
  private socket: WebSocket | null = null;
  private retryWait = 1000;
  private instance_id = getRandomInt32();
  private shouldReconnect = true;
  private lastUrl: string | null = null;

  private handlers: CommsMessageHandler[] = [];
  private connectHandlers: CommsConnectHandler[] = [];
  // Serialize handler dispatch so message order is preserved across awaits.
  private dispatchChain: Promise<void> = Promise.resolve();

  isConnected(): boolean {
    return this.socket?.readyState === WebSocket.OPEN;
  }

  getInstanceId(): number {
    return this.instance_id;
  }

  onMessage(handler: CommsMessageHandler): Unsubscribe {
    this.handlers.push(handler);
    return () => {
      const i = this.handlers.indexOf(handler);
      if (i >= 0) this.handlers.splice(i, 1);
    };
  }

  onConnect(handler: CommsConnectHandler): Unsubscribe {
    this.connectHandlers.push(handler);
    return () => {
      const i = this.connectHandlers.indexOf(handler);
      if (i >= 0) this.connectHandlers.splice(i, 1);
    };
  }

  async connect(url: string): Promise<void> {
    const statusStore = useWebsocketStatusStore.getState();

    const overrideId = runtime.websocketId();
    if (overrideId !== undefined && overrideId !== "") {
      console.log("WebSocket ID Override:", overrideId);
      this.instance_id = normalizeInstanceId(Number(overrideId));
    }

    this.lastUrl = url;
    const wsUrl = `${url}?client-type=web&instance-id=${this.instance_id}`;
    this.shouldReconnect = true;

    try {
      this.socket = new WebSocket(wsUrl);
      console.log("WebSocket connecting to:", wsUrl);

      this.socket.addEventListener("message", (evt) => this.onSocketMessage(evt));
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
      this.socket.addEventListener("error", () => {
        // open()/error promise below schedules reconnect
      });

      await new Promise<void>((resolve, reject) => {
        this.socket!.addEventListener("open", () => {
          console.log("WebSocket connected");
          statusStore.setConnected(true);
          statusStore.setFrontendId(this.instance_id);
          for (const handler of this.connectHandlers) {
            try {
              const r = handler();
              if (r && typeof (r as Promise<void>).then === "function") {
                (r as Promise<void>).catch((err) =>
                  console.error("onConnect handler error:", err),
                );
              }
            } catch (err) {
              console.error("onConnect handler error:", err);
            }
          }
          resolve();
        });
        this.socket!.addEventListener("error", (ev) => reject(ev));
      });
    } catch (err) {
      // Swallowed; the close handler will retry.
    }
  }

  async disconnect(): Promise<void> {
    this.shouldReconnect = false;
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
  }

  async sendCommand(payload: Uint8Array): Promise<void> {
    if (!this.isConnected()) {
      throw new Error("WebSocket is not open");
    }
    this.socket!.send(payload);
  }

  async setInstanceId(newId: number, reconnect: boolean = true): Promise<void> {
    if (typeof newId !== "number" || !Number.isFinite(newId)) {
      throw new Error("Instance ID must be a finite number");
    }
    newId = Math.trunc(newId);
    if (newId < INT32_MIN || newId > INT32_MAX) {
      throw new Error(`Instance ID must be int32 (${INT32_MIN}..${INT32_MAX})`);
    }
    if (newId <= 0) newId = Math.max(1, Math.abs(newId));

    this.instance_id = newId;
    window.WEBSOCKET_ID = newId;
    useWebsocketStatusStore.getState().setFrontendId(newId);

    if (reconnect && this.lastUrl) {
      try {
        if (this.socket) await this.disconnect();
      } catch (_) {
        // ignore
      }
      await this.connect(this.lastUrl);
    }
  }

  private onSocketMessage(evt: MessageEvent): void {
    if (!(evt.data instanceof Blob)) {
      console.log("Message from server", evt.data);
      return;
    }
    // Chain the handler dispatch so ordering is preserved across awaits.
    this.dispatchChain = this.dispatchChain.then(async () => {
      try {
        const buffer = await evt.data.arrayBuffer();
        for (const handler of this.handlers) {
          await handler(buffer);
        }
      } catch (err) {
        console.error("Error handling WebSocket message:", err);
      }
    });
  }
}
