import assert from "node:assert/strict";
import { test } from "node:test";
import * as flatbuffers from "flatbuffers";

// LIST_PROCEDURAL_MODELS / LOAD_PROCEDURAL_MODEL (ws/REST parity plan, step 6): the local-disk
// model browser's read half of SAVE_PROCEDURAL_MODEL. Same fake-transport shape as
// `wsProceduralCommit.test.ts` -- a real `RequestCorrelator` wired to an in-memory answer, so the
// assertion is on what `WSProceduralModelCapability` actually sends and how it decodes the reply.

import { CommandType } from "@/flatbuffers/commands/command-type";
import { Message } from "@/flatbuffers/wsock/message";
import { ServerReply } from "@/flatbuffers/server/server-reply";
import { ProceduralModelLoadReply } from "@/flatbuffers/server/procedural-model-load-reply";
import { ProceduralModelListReply } from "@/flatbuffers/server/procedural-model-list-reply";
import { ProceduralModelListEntry } from "@/flatbuffers/server/procedural-model-list-entry";
import { Error as FbError } from "@/flatbuffers/base/error";
import { parseMessage, requestIdOf, RequestCorrelator } from "@/utils/comms/wsRequests";
import { useWebsocketStatusStore } from "@/state/websocketStatusStore";
import { WSProceduralModelCapability } from "@/services/capabilities/ws_capabilities";
import { CapabilityUnavailableError, LOCAL_MODEL_SCOPE } from "@/services/capabilities/types";
import type { ProceduralDoc } from "@/services/viewerApi";
import type { Comms } from "@/utils/comms";

const DOC = { spaces: [{ NAME: "Deck1" }], equipments: [] } as unknown as ProceduralDoc;

function buildLoadSuccessReply(requestId: string, modelId: string, docJson: string, contentHash: string): ArrayBuffer {
  const builder = new flatbuffers.Builder(512);
  const idOffset = builder.createString(requestId);
  const modelIdOffset = builder.createString(modelId);
  const docJsonOffset = builder.createString(docJson);
  const hashOffset = builder.createString(contentHash);
  ProceduralModelLoadReply.startProceduralModelLoadReply(builder);
  ProceduralModelLoadReply.addModelId(builder, modelIdOffset);
  ProceduralModelLoadReply.addDocJson(builder, docJsonOffset);
  ProceduralModelLoadReply.addContentHash(builder, hashOffset);
  const loadReplyOffset = ProceduralModelLoadReply.endProceduralModelLoadReply(builder);

  ServerReply.startServerReply(builder);
  ServerReply.addReplyTo(builder, CommandType.LOAD_PROCEDURAL_MODEL);
  ServerReply.addLoadProceduralModel(builder, loadReplyOffset);
  const serverReplyOffset = ServerReply.endServerReply(builder);

  Message.startMessage(builder);
  Message.addCommandType(builder, CommandType.SERVER_REPLY);
  Message.addServerReply(builder, serverReplyOffset);
  Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice().buffer;
}

function buildErrorReply(requestId: string, message: string): ArrayBuffer {
  const builder = new flatbuffers.Builder(512);
  const idOffset = builder.createString(requestId);
  const messageOffset = builder.createString(message);
  FbError.startError(builder);
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

function buildListReply(
  requestId: string,
  entries: { modelId: string; contentHash: string; modifiedAt: number; sizeBytes: number }[],
): ArrayBuffer {
  const builder = new flatbuffers.Builder(1024);
  const idOffset = builder.createString(requestId);
  const entryOffsets = entries.map((e) => {
    const modelIdOffset = builder.createString(e.modelId);
    const hashOffset = builder.createString(e.contentHash);
    ProceduralModelListEntry.startProceduralModelListEntry(builder);
    ProceduralModelListEntry.addModelId(builder, modelIdOffset);
    ProceduralModelListEntry.addContentHash(builder, hashOffset);
    ProceduralModelListEntry.addModifiedAt(builder, BigInt(e.modifiedAt));
    ProceduralModelListEntry.addSizeBytes(builder, BigInt(e.sizeBytes));
    return ProceduralModelListEntry.endProceduralModelListEntry(builder);
  });
  const entriesVector = ProceduralModelListReply.createEntriesVector(builder, entryOffsets);
  ProceduralModelListReply.startProceduralModelListReply(builder);
  ProceduralModelListReply.addEntries(builder, entriesVector);
  const listReplyOffset = ProceduralModelListReply.endProceduralModelListReply(builder);

  ServerReply.startServerReply(builder);
  ServerReply.addReplyTo(builder, CommandType.LIST_PROCEDURAL_MODELS);
  ServerReply.addListProceduralModels(builder, listReplyOffset);
  const serverReplyOffset = ServerReply.endServerReply(builder);

  Message.startMessage(builder);
  Message.addCommandType(builder, CommandType.SERVER_REPLY);
  Message.addServerReply(builder, serverReplyOffset);
  Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice().buffer;
}

/** A transport that answers whatever `answer(requestId)` returns, routed through a real
 * `RequestCorrelator` exactly like the production transports use (same helper shape as
 * `wsProceduralCommit.test.ts`'s `fakeServerTransport`). */
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

test("fetchModel resolves a document over LOAD_PROCEDURAL_MODEL", async () => {
  useWebsocketStatusStore.getState().setConnected(true);
  const { sent, transport } = fakeServerTransport((id) =>
    buildLoadSuccessReply(id, "hull-a", JSON.stringify(DOC), "abc123"),
  );
  const cap = new WSProceduralModelCapability(transport);

  const result = await cap.fetchModel({ scope: LOCAL_MODEL_SCOPE, modelId: "hull-a" });

  assert.equal(result.available, true);
  assert.deepEqual(result.doc, DOC);
  assert.equal(sent.length, 1);
  const command = parseMessage(sent[0].slice().buffer);
  assert.equal(command.commandType(), CommandType.LOAD_PROCEDURAL_MODEL);
  assert.equal(command.server()?.loadProceduralModel()?.modelId(), "hull-a");

  useWebsocketStatusStore.getState().setConnected(false);
});

test("fetchModel seeds knownHashes from the load reply, for the next commitModel", async () => {
  // Drives TWO verbs (load, then commit) through the same capability instance and transport, so
  // the fake transport routes by the outgoing command's own type rather than a fixed answer.
  useWebsocketStatusStore.getState().setConnected(true);
  const sent: Uint8Array[] = [];
  const correlator = new RequestCorrelator((payload) => {
    sent.push(payload);
    const command = parseMessage(payload.slice().buffer);
    const id = requestIdOf(command);
    assert.ok(id, "command carries a request id");
    queueMicrotask(() => {
      if (command.commandType() === CommandType.LOAD_PROCEDURAL_MODEL) {
        correlator.settle(parseMessage(buildLoadSuccessReply(id!, "hull-a", JSON.stringify(DOC), "abc123")));
        return;
      }
      const builder = new flatbuffers.Builder(256);
      const idOffset = builder.createString(id!);
      Message.startMessage(builder);
      Message.addCommandType(builder, CommandType.SERVER_REPLY);
      Message.addRequestId(builder, idOffset);
      builder.finish(Message.endMessage(builder));
      correlator.settle(parseMessage(builder.asUint8Array().slice().buffer));
    });
  });
  const transport = {
    request: (build, opts) => correlator.request(build, opts),
    getInstanceId: () => 4321,
  } as Pick<Comms, "request" | "getInstanceId"> as Comms;
  const cap = new WSProceduralModelCapability(transport);

  await cap.fetchModel({ scope: LOCAL_MODEL_SCOPE, modelId: "hull-a" });
  await cap.commitModel(LOCAL_MODEL_SCOPE, "hull-a", DOC, 0);

  assert.equal(sent.length, 2);
  const commitCommand = parseMessage(sent[1].slice().buffer);
  assert.equal(
    commitCommand.server()?.saveProceduralModel()?.expectedContentHash(),
    "abc123",
    "commit sends the hash the load reported, not a blind first save",
  );

  useWebsocketStatusStore.getState().setConnected(false);
});

test("fetchModel falls back to the embedded document when the load fails", async () => {
  useWebsocketStatusStore.getState().setConnected(true);
  const { transport } = fakeServerTransport((id) => buildErrorReply(id, "no procedural model has been saved under model_id 'missing'"));
  const cap = new WSProceduralModelCapability(transport);
  cap.adoptEmbeddedModel(DOC);

  const result = await cap.fetchModel({ scope: LOCAL_MODEL_SCOPE, modelId: "missing" });

  assert.equal(result.available, true, "falls back to the embedded doc rather than surfacing an error");
  assert.deepEqual(result.doc, DOC);

  useWebsocketStatusStore.getState().setConnected(false);
});

test("fetchModel with no modelId never sends a request, same as before this verb existed", async () => {
  const { sent, transport } = fakeServerTransport(() => {
    throw new Error("should never be called");
  });
  const cap = new WSProceduralModelCapability(transport);

  assert.deepEqual(await cap.fetchModel({}), { available: false });
  assert.equal(sent.length, 0);
});

test("listModels resolves the entries LIST_PROCEDURAL_MODELS reports", async () => {
  useWebsocketStatusStore.getState().setConnected(true);
  const { sent, transport } = fakeServerTransport((id) =>
    buildListReply(id, [
      { modelId: "hull-a", contentHash: "abc123", modifiedAt: 1_700_000_000_000, sizeBytes: 42 },
      { modelId: "hull-b", contentHash: "def456", modifiedAt: 1_700_000_001_000, sizeBytes: 84 },
    ]),
  );
  const cap = new WSProceduralModelCapability(transport);

  const entries = await cap.listModels(LOCAL_MODEL_SCOPE);

  assert.equal(sent.length, 1);
  const command = parseMessage(sent[0].slice().buffer);
  assert.equal(command.commandType(), CommandType.LIST_PROCEDURAL_MODELS);
  assert.deepEqual(entries, [
    { modelId: "hull-a", contentHash: "abc123", modifiedAt: 1_700_000_000_000, sizeBytes: 42 },
    { modelId: "hull-b", contentHash: "def456", modifiedAt: 1_700_000_001_000, sizeBytes: 84 },
  ]);

  useWebsocketStatusStore.getState().setConnected(false);
});

test("listModels resolves an empty array for an empty directory", async () => {
  useWebsocketStatusStore.getState().setConnected(true);
  const { transport } = fakeServerTransport((id) => buildListReply(id, []));
  const cap = new WSProceduralModelCapability(transport);

  assert.deepEqual(await cap.listModels(LOCAL_MODEL_SCOPE), []);

  useWebsocketStatusStore.getState().setConnected(false);
});

test("listModels refuses immediately when the socket is disconnected, without sending anything", async () => {
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

  await assert.rejects(cap.listModels(LOCAL_MODEL_SCOPE), CapabilityUnavailableError);
  assert.equal(requestCalled, false, "a disconnected socket must not attempt the send");
});

test("supports() reports listModels alongside commitModel", () => {
  const cap = new WSProceduralModelCapability();
  assert.equal(cap.supports("listModels"), true);
  assert.equal(cap.supports("commitModel"), true);
  assert.equal(cap.supports("compileModel"), false);
});
