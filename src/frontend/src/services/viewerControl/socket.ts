// The hosted viewer's leg of viewer control: a websocket to the REST server (`/api/viewer/ws`). The
// tab asks for a single-use ticket (an authenticated POST: a browser's websocket cannot carry the
// bearer header), opens the socket with it, and is then a session of its user for as long as the
// socket lives. Commands arrive as VIEWER_COMMAND; each is run (./answer.ts) and answered with
// VIEWER_ANSWER; what the tab shows is sent as VIEWER_STATE when it opens and when it changes. A
// socket that drops is opened again, with a new ticket, after `retryMs`.
//
// Started only while external controls are on (./gate.ts). The desktop viewer needs none of this:
// it already holds a websocket to its server, and answers VIEWER_COMMAND there
// (utils/fb_handling/handle_incoming_buffers.ts).

import { answerPayload } from "./answer";
import { collectViewerState, type ViewerControlDeps } from "./commands";
import { answerBytes, parseBytes, readViewerCommand, stateBytes } from "./wire";

/** The part of a WebSocket this uses. */
export interface SocketLike {
  binaryType: string;
  readonly readyState: number;
  send(data: Uint8Array): void;
  close(code?: number): void;
  addEventListener(type: "open" | "close" | "error", listener: () => void): void;
  addEventListener(type: "message", listener: (e: { data: unknown }) => void): void;
}

export interface ViewerSocketIO {
  /** A ticket for the socket, and the path to open it on (`POST /api/viewer/ws-ticket`). */
  ticket(client: Record<string, unknown>): Promise<{ ticket: string; path: string }>;
  /** Open a websocket on `url`. */
  open(url: string): SocketLike;
  /** The websocket URL for `path` and `ticket`. */
  url(path: string, ticket: string): string;
}

export interface ViewerSocketOptions {
  watch?: (changed: () => void) => () => void;
  client?: Record<string, unknown>;
  retryMs?: number;
  reportDelayMs?: number;
}

const OPEN = 1;

/** Serve this tab over a websocket until the returned stop is called. */
export function startViewerSocket(deps: ViewerControlDeps, io: ViewerSocketIO, opts: ViewerSocketOptions = {}): () => void {
  let stopped = false;
  let socket: SocketLike | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let reportTimer: ReturnType<typeof setTimeout> | null = null;
  let lastState = "";
  // Commands are answered one after another, in the order they came.
  let chain: Promise<void> = Promise.resolve();

  const sendState = () => {
    reportTimer = null;
    if (!socket || socket.readyState !== OPEN) return;
    let state: unknown;
    try {
      state = collectViewerState(deps);
    } catch {
      return;
    }
    const text = JSON.stringify(state);
    if (text === lastState) return;
    lastState = text;
    socket.send(stateBytes(state));
  };

  const scheduleReconnect = () => {
    if (stopped || retry !== null) return;
    retry = setTimeout(() => {
      retry = null;
      void connect();
    }, opts.retryMs ?? 5000);
  };

  const onMessage = (data: unknown) => {
    const bytes = data instanceof ArrayBuffer ? data : data instanceof Uint8Array ? data : null;
    if (!bytes) return;
    let command;
    try {
      command = readViewerCommand(parseBytes(bytes));
    } catch {
      return;
    }
    if (!command) return;
    const { requestId, payload } = command;
    const from = socket;
    chain = chain.then(async () => {
      const answer = await answerPayload(deps, payload);
      if (from && from.readyState === OPEN) {
        from.send(answerBytes(requestId, answer, { kind: "server" }));
        lastState = JSON.stringify(answer.state);
      }
    });
  };

  async function connect(): Promise<void> {
    if (stopped) return;
    let ticket: { ticket: string; path: string };
    try {
      ticket = await io.ticket(opts.client ?? {});
    } catch {
      scheduleReconnect();
      return;
    }
    if (stopped) return;
    const ws = io.open(io.url(ticket.path, ticket.ticket));
    ws.binaryType = "arraybuffer";
    socket = ws;
    lastState = "";
    ws.addEventListener("open", () => sendState());
    ws.addEventListener("message", (e) => onMessage(e.data));
    ws.addEventListener("close", () => {
      if (socket === ws) socket = null;
      scheduleReconnect();
    });
  }

  const unwatch = opts.watch?.(() => {
    if (reportTimer !== null || stopped) return;
    reportTimer = setTimeout(sendState, opts.reportDelayMs ?? 1000);
  });
  void connect();

  return () => {
    stopped = true;
    unwatch?.();
    if (retry !== null) clearTimeout(retry);
    if (reportTimer !== null) clearTimeout(reportTimer);
    socket?.close(1000);
    socket = null;
  };
}
