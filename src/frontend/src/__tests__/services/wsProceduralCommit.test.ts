import assert from "node:assert/strict";
import { test } from "node:test";
import * as flatbuffers from "flatbuffers";

// SAVE_PROCEDURAL_MODEL (ws/REST parity plan, step 3): `WSProceduralModelCapability.commitModel`
// is the first websocket verb that actually sends bytes and awaits a correlated reply, rather than
// reading something the GLB already carried. These pin its three outcomes against a fake
// transport, the same shape `requestListOfNodes.test.ts` uses for the reference verb conversion:
//   - a normal SERVER_REPLY resolves with the new hash,
//   - an ERROR reply with code 409 becomes a typed `ProceduralCommitConflictError`,
//   - a disconnected socket (`canEdit` false) never sends anything and throws
//     `CapabilityUnavailableError` instead of surfacing a raw transport failure.
//
// `WSProceduralModelCapability` deliberately never statically imports `@/utils/comms` (see its
// module comment), so — unlike `requestListOfNodes.test.ts` — this file does not need to stub
// `window`/`sessionStorage` before importing it.

import { CommandType } from "@/flatbuffers/commands/command-type";
import { Message } from "@/flatbuffers/wsock/message";
import { ServerReply } from "@/flatbuffers/server/server-reply";
import { ProceduralModelSaveReply } from "@/flatbuffers/server/procedural-model-save-reply";
import { Error as FbError } from "@/flatbuffers/base/error";
import { parseMessage, requestIdOf, RequestCorrelator } from "@/utils/comms/wsRequests";
import { useWebsocketStatusStore } from "@/state/websocketStatusStore";
import { WSProceduralModelCapability } from "@/services/capabilities/ws_capabilities";
import { CapabilityUnavailableError, ProceduralCommitConflictError, LOCAL_MODEL_SCOPE } from "@/services/capabilities/types";
import type { ProceduralDoc } from "@/services/viewerApi";
import type { Comms } from "@/utils/comms";

const DOC = { spaces: [], equipments: [] } as unknown as ProceduralDoc;

function buildSuccessReply(requestId: string, modelId: string, contentHash: string): ArrayBuffer {
  const builder = new flatbuffers.Builder(512);
  const idOffset = builder.createString(requestId);
  const modelIdOffset = builder.createString(modelId);
  const hashOffset = builder.createString(contentHash);
  ProceduralModelSaveReply.startProceduralModelSaveReply(builder);
  ProceduralModelSaveReply.addModelId(builder, modelIdOffset);
  ProceduralModelSaveReply.addContentHash(builder, hashOffset);
  const saveReplyOffset = ProceduralModelSaveReply.endProceduralModelSaveReply(builder);

  ServerReply.startServerReply(builder);
  ServerReply.addReplyTo(builder, CommandType.SAVE_PROCEDURAL_MODEL);
  ServerReply.addSaveProceduralModel(builder, saveReplyOffset);
  const serverReplyOffset = ServerReply.endServerReply(builder);

  Message.startMessage(builder);
  Message.addCommandType(builder, CommandType.SERVER_REPLY);
  Message.addServerReply(builder, serverReplyOffset);
  Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice().buffer;
}

function buildErrorReply(requestId: string, code: number, message: string): ArrayBuffer {
  const builder = new flatbuffers.Builder(512);
  const idOffset = builder.createString(requestId);
  const messageOffset = builder.createString(message);
  FbError.startError(builder);
  FbError.addCode(builder, code);
  FbError.addMessage(builder, messageOffset);
  const errorOffset = FbError.endError(builder);

  ServerReply.startServerReply(builder);
  ServerReply.addError(builder, errorOffset);
  const serverReplyOffset = ServerReply.endServerReply(builder);

  Message.startMessage(builder);
  Message.addCommandType(builder, CommandType.ERROR);
  Message.addServerReply(builder, serverReplyOffset);
  Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice().buffer;
}

/** A transport that answers whatever `answer(requestId)` returns for the one command it expects
 * to see, routed through a real `RequestCorrelator` exactly like the production transports use. */
function fakeServerTransport(answer: (requestId: string) => ArrayBuffer) {
  const sent: Uint8Array[] = [];
  const correlator = new RequestCorrelator((payload) => {
    sent.push(payload);
    const id = requestIdOf(parseMessage(payload.slice().buffer));
    assert.ok(id, "command carries a request id");
    queueMicrotask(() => {
      correlator.settle(parseMessage(answer(id)));
    });
  });
  const transport = {
    request: (build, opts) => correlator.request(build, opts),
    getInstanceId: () => 4321,
  } as Pick<Comms, "request" | "getInstanceId"> as Comms;
  return { sent, transport };
}

test("commitModel resolves on a normal SERVER_REPLY and sends the request over SAVE_PROCEDURAL_MODEL", async () => {
  useWebsocketStatusStore.getState().setConnected(true);
  const { sent, transport } = fakeServerTransport((id) => buildSuccessReply(id, "hull-a", "abc123"));
  const cap = new WSProceduralModelCapability(transport);

  const result = await cap.commitModel(LOCAL_MODEL_SCOPE, "hull-a", DOC, 0);

  assert.equal(result.id, "hull-a");
  assert.equal(result.revision, 0, "the ws implementation does not invent a numeric revision");
  assert.equal(sent.length, 1);
  const command = parseMessage(sent[0].slice().buffer);
  assert.equal(command.commandType(), CommandType.SAVE_PROCEDURAL_MODEL);
  const payload = command.server()?.saveProceduralModel();
  assert.equal(payload?.modelId(), "hull-a");
  assert.equal(payload?.docJson(), JSON.stringify(DOC));
  assert.equal(payload?.expectedContentHash(), null, "no known hash yet for a first save");

  useWebsocketStatusStore.getState().setConnected(false);
});

test("a second commit for the same model_id sends back the hash the first one returned", async () => {
  useWebsocketStatusStore.getState().setConnected(true);
  const hashes = ["hash-1", "hash-2"];
  let call = 0;
  const { sent, transport } = fakeServerTransport((id) => buildSuccessReply(id, "hull-a", hashes[call++]));
  const cap = new WSProceduralModelCapability(transport);

  await cap.commitModel(LOCAL_MODEL_SCOPE, "hull-a", DOC, 0);
  await cap.commitModel(LOCAL_MODEL_SCOPE, "hull-a", { ...DOC, spaces: [{ NAME: "S1" }] } as ProceduralDoc, 0);

  assert.equal(sent.length, 2);
  const second = parseMessage(sent[1].slice().buffer);
  assert.equal(second.server()?.saveProceduralModel()?.expectedContentHash(), "hash-1");

  useWebsocketStatusStore.getState().setConnected(false);
});

test("commitModel turns a 409 ERROR reply into a typed conflict error", async () => {
  useWebsocketStatusStore.getState().setConnected(true);
  const { transport } = fakeServerTransport((id) => buildErrorReply(id, 409, "revision conflict"));
  const cap = new WSProceduralModelCapability(transport);

  await assert.rejects(
    cap.commitModel(LOCAL_MODEL_SCOPE, "hull-a", DOC, 3),
    (err: unknown) => {
      assert.ok(err instanceof ProceduralCommitConflictError);
      assert.equal(err.modelId, "hull-a");
      return true;
    },
  );

  useWebsocketStatusStore.getState().setConnected(false);
});

test("commitModel surfaces a non-conflict ERROR reply as a plain Error, not a conflict", async () => {
  useWebsocketStatusStore.getState().setConnected(true);
  const { transport } = fakeServerTransport((id) => buildErrorReply(id, 0, "invalid model_id"));
  const cap = new WSProceduralModelCapability(transport);

  await assert.rejects(
    cap.commitModel(LOCAL_MODEL_SCOPE, "../evil", DOC, 0),
    (err: unknown) => {
      assert.ok(!(err instanceof ProceduralCommitConflictError));
      assert.ok(err instanceof Error);
      assert.match(err.message, /invalid model_id/);
      return true;
    },
  );

  useWebsocketStatusStore.getState().setConnected(false);
});

test("commitModel refuses immediately when the socket is disconnected, without sending anything", async () => {
  useWebsocketStatusStore.getState().setConnected(false);
  let requestCalled = false;
  const transport = {
    request: () => {
      requestCalled = true;
      return Promise.reject(new Error("should never be called"));
    },
    getInstanceId: () => 1,
  } as Pick<Comms, "request" | "getInstanceId"> as Comms;
  const cap = new WSProceduralModelCapability(transport);

  await assert.rejects(cap.commitModel(LOCAL_MODEL_SCOPE, "hull-a", DOC, 0), CapabilityUnavailableError);
  assert.equal(requestCalled, false, "a disconnected socket must not attempt the send");
});

test("supports() reports commitModel and nothing else", () => {
  const cap = new WSProceduralModelCapability();
  assert.equal(cap.supports("commitModel"), true);
  assert.equal(cap.supports("compileModel"), false);
  assert.equal(cap.supports("previewModel"), false);
  assert.equal(cap.supports("resyncEquipmentTypes"), false);
});
