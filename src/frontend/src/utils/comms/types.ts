// Transport-agnostic comms interface. Both the WebSocket implementation and
// the future REST implementation satisfy this. Domain code should depend on
// this interface only, never on a concrete transport.

export type CommsMessageHandler = (buffer: ArrayBuffer) => void | Promise<void>;
export type CommsConnectHandler = () => void | Promise<void>;
export type Unsubscribe = () => void;

export interface Comms {
  connect(url: string): Promise<void>;
  disconnect(): Promise<void>;
  isConnected(): boolean;

  // Send a serialized flatbuffer Message. The transport returns when the
  // bytes have been handed off (WS: socket.send; REST: response received).
  sendCommand(payload: Uint8Array): Promise<void>;

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
