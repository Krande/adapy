// Request/response correlation for the flatbuffer Message envelope.
//
// A command carries a client-assigned `request_id`; the server echoes it on
// the reply that answers it (and on nothing else). This module keeps the map
// of requests still waiting for that echo. It is transport-agnostic and
// deliberately free of three/React/zustand so it runs under a bare node
// process: both WSComms and RESTComms own one instance and call `settle`
// on every incoming buffer BEFORE handing it to the general dispatcher, so
// a correlated reply resolves its promise and only uncorrelated messages
// (unsolicited pushes, replies to fire-and-forget verbs, older servers that
// do not echo) fall through to the `switch(replyTo)` in
// handle_incoming_buffers.ts.
import * as flatbuffers from "flatbuffers";
import { Message } from "@/flatbuffers/wsock/message";
import { CommandType } from "@/flatbuffers/commands/command-type";

export interface RequestOptions {
  // Reject the request if no reply with its id arrives within this window.
  timeoutMs?: number;
}

export type BuildRequest = (requestId: string) => Uint8Array;

export const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

interface Pending {
  resolve: (message: Message) => void;
  reject: (err: Error) => void;
  timer: ReturnType<typeof setTimeout> | null;
}

// Thrown (as the rejection reason) when the reply to a request is an ERROR
// message from the server. Carries the decoded reply so a caller can still
// inspect it.
export class ServerReplyError extends Error {
  constructor(
    message: string,
    public readonly reply: Message,
  ) {
    super(message);
    this.name = "ServerReplyError";
  }
}

export function parseMessage(buffer: ArrayBuffer): Message {
  return Message.getRootAsMessage(new flatbuffers.ByteBuffer(new Uint8Array(buffer)));
}

// Reads the correlation id off a decoded message; "" and null both mean
// "not correlated" (the server omits the field on unsolicited pushes, and an
// older server never sets it at all).
export function requestIdOf(message: Message): string | null {
  const id = message.requestId();
  return id ? id : null;
}

let idCounter = 0;

export function nextRequestId(): string {
  idCounter += 1;
  const cryptoObj = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  const entropy =
    cryptoObj && typeof cryptoObj.randomUUID === "function"
      ? cryptoObj.randomUUID().slice(0, 8)
      : Math.random().toString(36).slice(2, 10);
  return `${entropy}-${idCounter}`;
}

export class RequestCorrelator {
  private readonly pending = new Map<string, Pending>();

  constructor(
    // Hands the built bytes to the transport. May throw / reject when the
    // transport is not connected; that failure becomes the request's rejection.
    private readonly send: (payload: Uint8Array) => Promise<void> | void,
    private readonly defaultTimeoutMs: number = DEFAULT_REQUEST_TIMEOUT_MS,
  ) {}

  get pendingCount(): number {
    return this.pending.size;
  }

  // Assigns a request id, lets `build` bake it into the message, sends it and
  // resolves with the reply that echoes the id. Rejects on timeout, when the
  // transport closes (`rejectAll`), when sending fails, or when the reply is a
  // server ERROR message.
  request(build: BuildRequest, opts?: RequestOptions): Promise<Message> {
    const requestId = nextRequestId();
    const timeoutMs = opts?.timeoutMs ?? this.defaultTimeoutMs;

    return new Promise<Message>((resolve, reject) => {
      const entry: Pending = { resolve, reject, timer: null };
      if (timeoutMs > 0 && Number.isFinite(timeoutMs)) {
        entry.timer = setTimeout(() => {
          this.pending.delete(requestId);
          reject(new Error(`Request ${requestId} timed out after ${timeoutMs} ms`));
        }, timeoutMs);
      }
      this.pending.set(requestId, entry);

      // Build and send synchronously so the bytes leave in call order with
      // any plain sendCommand() around them; only the failure is async.
      try {
        const result = this.send(build(requestId));
        if (result && typeof (result as Promise<void>).then === "function") {
          (result as Promise<void>).catch((err) => this.fail(requestId, err));
        }
      } catch (err) {
        this.fail(requestId, err);
      }
    });
  }

  // Routes an incoming message to the request it answers. Returns true when a
  // pending request consumed it (the caller must NOT dispatch it further) and
  // false when it is uncorrelated or answers a request no longer pending.
  settle(message: Message): boolean {
    const requestId = requestIdOf(message);
    if (requestId === null) return false;
    const entry = this.take(requestId);
    if (!entry) return false;
    if (message.commandType() === CommandType.ERROR) {
      const detail = message.serverReply()?.error()?.message() ?? "server returned an error";
      entry.reject(new ServerReplyError(String(detail), message));
    } else {
      entry.resolve(message);
    }
    return true;
  }

  // Rejects every pending request; used when the transport closes so callers
  // are not left waiting for a reply that can no longer arrive.
  rejectAll(reason: Error): void {
    for (const [requestId, entry] of this.pending) {
      this.pending.delete(requestId);
      if (entry.timer !== null) clearTimeout(entry.timer);
      entry.reject(reason);
    }
  }

  private take(requestId: string): Pending | undefined {
    const entry = this.pending.get(requestId);
    if (!entry) return undefined;
    this.pending.delete(requestId);
    if (entry.timer !== null) clearTimeout(entry.timer);
    return entry;
  }

  private fail(requestId: string, err: unknown): void {
    const entry = this.take(requestId);
    if (!entry) return;
    entry.reject(err instanceof Error ? err : new Error(String(err)));
  }
}
