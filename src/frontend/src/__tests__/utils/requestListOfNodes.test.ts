import assert from "node:assert/strict";
import { test } from "node:test";
import * as flatbuffers from "flatbuffers";

// The comms singleton reads `window.COMMS_MODE` when its module loads and pulls
// in the auth module, which reads `sessionStorage` at load, so both are stubbed
// BEFORE the dynamic imports below (the same shape the job-tracking test uses).
const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { window: unknown; sessionStorage: unknown; localStorage: unknown };
globals.window = globalThis;
globals.sessionStorage = storage;
globals.localStorage = storage;

const { Message } = await import("@/flatbuffers/wsock/message");
const { CommandType } = await import("@/flatbuffers/commands/command-type");
const { ServerReply } = await import("@/flatbuffers/server/server-reply");
const { ProcedureStore } = await import("@/flatbuffers/procedures/procedure-store");
const { Procedure } = await import("@/flatbuffers/procedures/procedure");
const { parseMessage, requestIdOf, RequestCorrelator } = await import("@/utils/comms/wsRequests");
const { request_list_of_nodes } = await import("@/utils/node_editor/handlers/request_list_of_nodes");
const { useNodeEditorStore } = await import("@/state/useNodeEditorStore");
type Comms = import("@/utils/comms").Comms;

// WHAT THIS IS FOR. `request_list_of_nodes` used to send its bytes and return
// before any reply existed; the node-editor store was filled in later by the
// verb-keyed dispatcher, and the caller had nothing to await. It is the first
// verb converted to `comms.request()`: the reply is correlated by request_id,
// handed back to the caller as the resolved value, and the store update still
// happens — done by the verb itself now, since a correlated reply is consumed
// before the dispatcher sees it.

function buildReply(requestId: string): ArrayBuffer {
  const builder = new flatbuffers.Builder(512);
  const idOffset = builder.createString(requestId);
  const nameOffset = builder.createString("add_stiffeners");
  Procedure.startProcedure(builder);
  Procedure.addName(builder, nameOffset);
  const procOffset = Procedure.endProcedure(builder);
  const procsVec = ProcedureStore.createProceduresVector(builder, [procOffset]);
  ProcedureStore.startProcedureStore(builder);
  ProcedureStore.addProcedures(builder, procsVec);
  const storeOffset = ProcedureStore.endProcedureStore(builder);
  ServerReply.startServerReply(builder);
  ServerReply.addReplyTo(builder, CommandType.LIST_PROCEDURES);
  const replyOffset = ServerReply.endServerReply(builder);
  Message.startMessage(builder);
  Message.addInstanceId(builder, 99);
  Message.addCommandType(builder, CommandType.SERVER_REPLY);
  Message.addProcedureStore(builder, storeOffset);
  Message.addServerReply(builder, replyOffset);
  Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice().buffer;
}

// A transport that behaves like the server: it answers each sent command with
// a reply echoing the command's request_id, routed through a real correlator.
function fakeServerTransport() {
  const sent: Uint8Array[] = [];
  const correlator = new RequestCorrelator((payload) => {
    sent.push(payload);
    const id = requestIdOf(parseMessage(payload.slice().buffer));
    assert.ok(id, "command carries a request id");
    queueMicrotask(() => {
      assert.equal(correlator.settle(parseMessage(buildReply(id))), true);
    });
  });
  const transport = {
    request: (build, opts) => correlator.request(build, opts),
    getInstanceId: () => 4321,
  } as Pick<Comms, "request" | "getInstanceId"> as Comms;
  return { sent, transport };
}

test("request_list_of_nodes awaits the correlated reply and still fills the store", async () => {
  useNodeEditorStore.getState().setNodes([]);
  const { sent, transport } = fakeServerTransport();

  const reply = await request_list_of_nodes(transport);

  assert.equal(sent.length, 1);
  const command = parseMessage(sent[0].slice().buffer);
  assert.equal(command.commandType(), CommandType.LIST_PROCEDURES);
  assert.equal(command.instanceId(), 4321);
  assert.equal(reply.requestId(), command.requestId(), "the resolved reply is the one echoing our id");
  assert.equal(reply.serverReply()?.replyTo(), CommandType.LIST_PROCEDURES);

  const nodes = useNodeEditorStore.getState().nodes;
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].type, "procedure");
  assert.equal(nodes[0].data.label, "add_stiffeners");
  useNodeEditorStore.getState().setNodes([]);
});

test("a transport failure surfaces to the caller instead of being swallowed", async () => {
  const transport = {
    request: () => Promise.reject(new Error("WebSocket is not open")),
    getInstanceId: () => 1,
  } as Pick<Comms, "request" | "getInstanceId"> as Comms;
  await assert.rejects(request_list_of_nodes(transport), /WebSocket is not open/);
});
