import assert from "node:assert/strict";
import { test } from "node:test";
import * as flatbuffers from "flatbuffers";

import { Message } from "@/flatbuffers/wsock/message";
import { CommandType } from "@/flatbuffers/commands/command-type";
import { ServerReply } from "@/flatbuffers/server/server-reply";
import { Error as FbError } from "@/flatbuffers/base/error";
import {
  RequestCorrelator,
  ServerReplyError,
  parseMessage,
  requestIdOf,
} from "@/utils/comms/wsRequests";

// WHAT THIS IS FOR. Until now no websocket request could be awaited: a verb sent
// its bytes and returned, and whichever reply came back was routed by verb
// (`switch(replyTo)`), so two requests of the same verb in flight were
// indistinguishable. `RequestCorrelator` is the pending-request map behind
// `comms.request()`; these tests drive it with hand-built Message buffers, the
// same shape the server (or a REST response) would put on the wire.

function buildCommand(requestId: string | null, commandType = CommandType.LIST_PROCEDURES): Uint8Array {
  const builder = new flatbuffers.Builder(256);
  const idOffset = requestId === null ? null : builder.createString(requestId);
  Message.startMessage(builder);
  Message.addInstanceId(builder, 7);
  Message.addCommandType(builder, commandType);
  if (idOffset !== null) Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array();
}

function buildReply(requestId: string | null, replyTo = CommandType.LIST_PROCEDURES): Message {
  const builder = new flatbuffers.Builder(256);
  const idOffset = requestId === null ? null : builder.createString(requestId);
  ServerReply.startServerReply(builder);
  ServerReply.addReplyTo(builder, replyTo);
  const replyOffset = ServerReply.endServerReply(builder);
  Message.startMessage(builder);
  Message.addInstanceId(builder, 99);
  Message.addCommandType(builder, CommandType.SERVER_REPLY);
  Message.addServerReply(builder, replyOffset);
  if (idOffset !== null) Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return parseMessage(builder.asUint8Array().slice().buffer);
}

function buildErrorReply(requestId: string, detail: string): Message {
  const builder = new flatbuffers.Builder(256);
  const idOffset = builder.createString(requestId);
  const detailOffset = builder.createString(detail);
  FbError.startError(builder);
  FbError.addMessage(builder, detailOffset);
  const errorOffset = FbError.endError(builder);
  ServerReply.startServerReply(builder);
  ServerReply.addError(builder, errorOffset);
  const replyOffset = ServerReply.endServerReply(builder);
  Message.startMessage(builder);
  Message.addCommandType(builder, CommandType.ERROR);
  Message.addServerReply(builder, replyOffset);
  Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return parseMessage(builder.asUint8Array().slice().buffer);
}

// A fake transport that records what was sent and lets the test pull the
// request id back out of the bytes exactly as the server would.
function fakeTransport() {
  const sent: Uint8Array[] = [];
  const correlator = new RequestCorrelator((payload) => {
    sent.push(payload);
  });
  const lastRequestId = (): string => {
    const buf = sent[sent.length - 1];
    const id = requestIdOf(parseMessage(buf.slice().buffer));
    assert.ok(id, "sent message carries a request id");
    return id;
  };
  return { sent, correlator, lastRequestId };
}

test("request sends bytes carrying a fresh id and resolves on the echoing reply", async () => {
  const { sent, correlator, lastRequestId } = fakeTransport();
  const promise = correlator.request((id) => buildCommand(id));
  assert.equal(sent.length, 1);
  assert.equal(correlator.pendingCount, 1);
  const id = lastRequestId();

  const reply = buildReply(id);
  assert.equal(correlator.settle(reply), true, "the correlated reply is consumed");
  const resolved = await promise;
  assert.equal(resolved.requestId(), id);
  assert.equal(resolved.serverReply()?.replyTo(), CommandType.LIST_PROCEDURES);
  assert.equal(correlator.pendingCount, 0);
});

test("two requests of the same verb in flight each get their own reply", async () => {
  const { correlator, lastRequestId } = fakeTransport();
  const first = correlator.request((id) => buildCommand(id));
  const firstId = lastRequestId();
  const second = correlator.request((id) => buildCommand(id));
  const secondId = lastRequestId();
  assert.notEqual(firstId, secondId);

  // Replies arrive out of order; each still lands on its own promise.
  correlator.settle(buildReply(secondId));
  correlator.settle(buildReply(firstId));
  assert.equal((await first).requestId(), firstId);
  assert.equal((await second).requestId(), secondId);
});

test("an uncorrelated message or an unknown id falls through (settle returns false)", async () => {
  const { correlator, lastRequestId } = fakeTransport();
  const promise = correlator.request((id) => buildCommand(id));
  const id = lastRequestId();

  assert.equal(correlator.settle(buildReply(null)), false, "no request_id: an unsolicited push");
  assert.equal(correlator.settle(buildReply("not-pending")), false, "unknown id");
  assert.equal(correlator.pendingCount, 1, "the real request is still waiting");

  correlator.settle(buildReply(id));
  await promise;
  assert.equal(correlator.settle(buildReply(id)), false, "a second reply with a settled id falls through");
});

test("a request rejects on timeout and is forgotten", async () => {
  const { correlator, lastRequestId } = fakeTransport();
  const promise = correlator.request((id) => buildCommand(id), { timeoutMs: 5 });
  const id = lastRequestId();
  await assert.rejects(promise, /timed out/);
  assert.equal(correlator.pendingCount, 0);
  assert.equal(correlator.settle(buildReply(id)), false, "a late reply falls through");
});

test("rejectAll (socket close) rejects every pending request with the given reason", async () => {
  const { correlator } = fakeTransport();
  const a = correlator.request((id) => buildCommand(id));
  const b = correlator.request((id) => buildCommand(id));
  correlator.rejectAll(new Error("socket closed"));
  await assert.rejects(a, /socket closed/);
  await assert.rejects(b, /socket closed/);
  assert.equal(correlator.pendingCount, 0);
});

test("a server ERROR reply rejects the request with the server's message", async () => {
  const { correlator, lastRequestId } = fakeTransport();
  const promise = correlator.request((id) => buildCommand(id));
  const id = lastRequestId();
  assert.equal(correlator.settle(buildErrorReply(id, "command not supported")), true);
  await assert.rejects(promise, (err: unknown) => {
    assert.ok(err instanceof ServerReplyError);
    assert.match(err.message, /command not supported/);
    assert.equal(err.reply.commandType(), CommandType.ERROR);
    return true;
  });
});

test("a failing send rejects the request instead of leaving it pending", async () => {
  const correlator = new RequestCorrelator(() => {
    throw new Error("WebSocket is not open");
  });
  await assert.rejects(
    correlator.request((id) => buildCommand(id)),
    /WebSocket is not open/,
  );
  assert.equal(correlator.pendingCount, 0);
});
