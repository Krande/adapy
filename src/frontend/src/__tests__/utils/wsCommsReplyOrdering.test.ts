import assert from "node:assert/strict";
import { test } from "node:test";
import * as flatbuffers from "flatbuffers";

// A pushed message whose handler sends a request and awaits its reply (OPEN_FEA_SOURCE reads the
// FEA manifest) must get that reply. WSComms runs handlers one at a time to keep pushes in order;
// a reply that waited its turn behind that chain waited on the very handler awaiting it, until the
// request timed out -- the viewer opened no FEA model and logged "Unknown Server Reply type".

(globalThis as any).window = { COMMS_MODE: undefined };

const { WSComms } = await import("@/utils/comms/ws_comms");
const { CommandType } = await import("@/flatbuffers/commands/command-type");
const { Message } = await import("@/flatbuffers/wsock/message");
const { parseMessage, requestIdOf } = await import("@/utils/comms/wsRequests");

function message(commandType: number, requestId?: string): Uint8Array {
  const builder = new flatbuffers.Builder(128);
  const idOffset = requestId ? builder.createString(requestId) : 0;
  Message.startMessage(builder);
  Message.addCommandType(builder, commandType);
  if (idOffset) Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice();
}

function deliver(comms: object, bytes: Uint8Array): void {
  (comms as any).onSocketMessage({ data: new Blob([bytes]) });
}

test("a handler can await a request of its own without the reply queueing behind it", async () => {
  const comms = new WSComms();
  const sent: string[] = [];
  (comms as any).sendCommand = async (payload: Uint8Array) => {
    sent.push(requestIdOf(parseMessage(payload.slice().buffer))!);
  };

  const handled: number[] = [];
  comms.onMessage(async (buffer) => {
    if (parseMessage(buffer).commandType() !== CommandType.OPEN_FEA_SOURCE) return;
    const reply = await comms.request((id) => message(CommandType.GET_FEA_MANIFEST, id), { timeoutMs: 2000 });
    handled.push(reply.commandType());
  });

  deliver(comms, message(CommandType.OPEN_FEA_SOURCE));
  // The handler is now running and waiting on its request.
  while (sent.length === 0) await new Promise((r) => setTimeout(r, 5));
  deliver(comms, message(CommandType.SERVER_REPLY, sent[0]));

  const deadline = Date.now() + 1000;
  while (handled.length === 0 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(handled, [CommandType.SERVER_REPLY], "the reply reached the handler well before the timeout");
});

test("pushes are still handled one at a time, in the order they arrived", async () => {
  const comms = new WSComms();
  const order: number[] = [];
  comms.onMessage(async (buffer) => {
    const type = parseMessage(buffer).commandType();
    if (type === CommandType.UPDATE_SCENE) await new Promise((r) => setTimeout(r, 30)); // a slow first push
    order.push(type);
  });

  deliver(comms, message(CommandType.UPDATE_SCENE));
  deliver(comms, message(CommandType.OPEN_FEA_SOURCE));
  await new Promise((r) => setTimeout(r, 100));

  assert.deepEqual(order, [CommandType.UPDATE_SCENE, CommandType.OPEN_FEA_SOURCE]);
});
