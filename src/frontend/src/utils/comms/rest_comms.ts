import { useWebsocketStatusStore } from "@/state/websocketStatusStore";
import { runtime } from "@/runtime/config";
import { getAccessToken } from "@/services/auth/oidc";
import { scopeUrlPart, useScopeStore } from "@/state/scopeStore";
import type {
  Comms,
  CommsConnectHandler,
  CommsMessageHandler,
  Unsubscribe,
} from "./types";

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

// HTTP transport for the same Message envelope used by WSComms. Each
// sendCommand is a POST to <apiBase>/rpc whose response body is a
// serialized Message that we feed back through onMessage handlers, so
// dispatch is identical to the WS path. Used by the hosted viewer mode.
export class RESTComms implements Comms {
  private apiBase: string = "/api";
  private connected = false;
  private instance_id = getRandomInt32();
  private inflight: AbortController[] = [];

  private handlers: CommsMessageHandler[] = [];
  private connectHandlers: CommsConnectHandler[] = [];
  // Serialize response dispatch so handler ordering matches request ordering.
  private dispatchChain: Promise<void> = Promise.resolve();

  isConnected(): boolean {
    return this.connected;
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
    const overrideId = runtime.websocketId();
    if (overrideId !== undefined && overrideId !== "") {
      this.instance_id = normalizeInstanceId(Number(overrideId));
    }

    // Trim trailing slash; an empty url falls back to /api.
    this.apiBase = (url || "/api").replace(/\/+$/, "") || "/api";
    this.connected = true;

    const statusStore = useWebsocketStatusStore.getState();
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
  }

  async disconnect(): Promise<void> {
    this.connected = false;
    for (const ctl of this.inflight) {
      try {
        ctl.abort();
      } catch (_) {
        // ignore
      }
    }
    this.inflight = [];

    const statusStore = useWebsocketStatusStore.getState();
    statusStore.setConnected(false);
    statusStore.setProcessInfo(null);
    statusStore.setConnectedClients([]);
  }

  async sendCommand(payload: Uint8Array): Promise<void> {
    if (!this.connected) throw new Error("REST comms not connected");

    const ctl = new AbortController();
    this.inflight.push(ctl);
    // 30s budget per request. Without this, a hung fetch (network
    // blip, tab in background long enough for the connection to be
    // collected, etc.) blocks the dispatchChain forever — every
    // subsequent response queues behind it without ever processing,
    // which is what users report as "refresh button does nothing
    // until I reload the page". Aborting on timeout lets the chain
    // drop the dead callback and move on.
    const timeoutId = window.setTimeout(() => {
      try { ctl.abort(); } catch (_) { /* ignore */ }
    }, 30_000);

    // Fire the request eagerly (matches WS socket.send semantics: caller
    // does not block on the response). Dispatch the response through the
    // chain so handler order matches request order.
    const headers: Record<string, string> = {
      "Content-Type": "application/octet-stream",
      // Defensive: a CDN / browser-cache should not be able to keep
      // serving an old listing response. POSTs are not normally
      // cached but some intermediaries treat them as cacheable when
      // the server emits stale Cache-Control. Force fresh every time.
      "Cache-Control": "no-store",
      // Phase 2C: scope rides on a header so the FlatBuffer envelope
      // stays untouched. The server resolves "user:me" to the caller's
      // sub before authorising.
      "X-Scope": scopeUrlPart(useScopeStore.getState().current),
    };
    const token = getAccessToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const respPromise = fetch(`${this.apiBase}/rpc`, {
      method: "POST",
      headers,
      body: payload,
      signal: ctl.signal,
      // Belt-and-braces: tell the browser cache layer to bypass too.
      cache: "no-store",
    });

    this.dispatchChain = this.dispatchChain.then(async () => {
      try {
        const resp = await respPromise;
        if (!resp.ok) {
          throw new Error(`REST rpc failed: HTTP ${resp.status}`);
        }
        // 204 / empty body means no Message to dispatch.
        if (resp.status === 204) return;
        const buffer = await resp.arrayBuffer();
        if (buffer.byteLength === 0) return;
        for (const handler of this.handlers) {
          await handler(buffer);
        }
      } catch (err: any) {
        if (err?.name !== "AbortError") {
          console.error("Error dispatching REST response:", err);
        }
      } finally {
        window.clearTimeout(timeoutId);
        const i = this.inflight.indexOf(ctl);
        if (i >= 0) this.inflight.splice(i, 1);
      }
    });
  }

  async setInstanceId(newId: number, _reconnect: boolean = true): Promise<void> {
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
    // REST is connectionless; no reconnect needed. The next sendCommand
    // will use the new id (callers read it from getInstanceId() at build time).
  }
}
