// Transport-agnostic comms interface. Both the WebSocket implementation and
// the future REST implementation satisfy this. Domain code should depend on
// this interface only, never on a concrete transport.

import type { Message } from "@/flatbuffers/wsock/message";
import type { BuildRequest, RequestOptions } from "./wsRequests";

export type CommsMessageHandler = (buffer: ArrayBuffer) => void | Promise<void>;
export type CommsConnectHandler = () => void | Promise<void>;
export type Unsubscribe = () => void;
export type { BuildRequest, RequestOptions } from "./wsRequests";

export interface Comms {
  connect(url: string): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;

  // Send a serialized flatbuffer Message. The transport returns when the
  // bytes have been handed off (WS: socket.send; REST: response received).
  sendCommand(payload: Uint8Array): Promise<void>;

  // Send a command and await the reply that echoes its request_id. `build`
  // receives the id and must bake it into the Message it serializes
  // (Message.addRequestId). Resolves with the correlated reply; rejects on
  // timeout, transport close, send failure, or a server ERROR reply. The
  // correlated reply is consumed here and is NOT passed to onMessage
  // handlers, so a caller that wants a store updated does that itself.
  request(build: BuildRequest, opts?: RequestOptions): Promise<Message>;

  // Register an incoming-message handler. Handlers are invoked sequentially
  // and awaited in order so dispatch ordering matches WS arrival order.
  // Returns an unsubscribe callback.
  onMessage(handler: CommsMessageHandler): Unsubscribe;

  // Fired after every successful connect (initial and reconnects). Used by
  // the app to issue status queries; keeping it outside the transport
  // avoids coupling ws_comms.ts to higher-level request helpers.
  onConnect(handler: CommsConnectHandler): Unsubscribe;

  getInstanceId(): number;
  setInstanceId(newId: number, reconnect?: boolean): Promise<void>;
}
