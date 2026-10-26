import assert from "node:assert/strict";
import { test } from "node:test";
import * as flatbuffers from "flatbuffers";

// GET_FEA_MANIFEST / GET_FEA_BLOB: the websocket side of the streaming FEA viewer
// (`FEAResult.show()`). Same fake-transport shape as `wsProceduralFetchAndList.test.ts` -- a real
// `RequestCorrelator` answered in memory, so the assertions are on what `WSFeaCapability` sends
// and how it decodes the reply into what the streaming loader consumes.

import { CommandType } from "@/flatbuffers/commands/command-type";
import { Message } from "@/flatbuffers/wsock/message";
import { ServerReply } from "@/flatbuffers/server/server-reply";
import { FeaManifestReply } from "@/flatbuffers/server/fea-manifest-reply";
import { FeaBlobReply } from "@/flatbuffers/server/fea-blob-reply";
import { parseMessage, requestIdOf, RequestCorrelator } from "@/utils/comms/wsRequests";
import { WSFeaCapability } from "@/services/capabilities/ws_capabilities";
import type { Comms } from "@/utils/comms";

const FILE = new Uint8Array(Array.from({ length: 32 }, (_, i) => i));

function finishReply(builder: flatbuffers.Builder, requestId: string, serverReplyOffset: number): ArrayBuffer {
  const idOffset = builder.createString(requestId);
  Message.startMessage(builder);
  Message.addCommandType(builder, CommandType.SERVER_REPLY);
  Message.addServerReply(builder, serverReplyOffset);
  Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice().buffer;
}

function manifestReply(requestId: string, sourceKey: string, bakeId: string, manifest: object): ArrayBuffer {
  const builder = new flatbuffers.Builder(512);
  const keyOffset = builder.createString(sourceKey);
  const bakeOffset = builder.createString(bakeId);
  const jsonOffset = builder.createString(JSON.stringify(manifest));
  FeaManifestReply.startFeaManifestReply(builder);
  FeaManifestReply.addSourceKey(builder, keyOffset);
  FeaManifestReply.addBakeId(builder, bakeOffset);
  FeaManifestReply.addManifestJson(builder, jsonOffset);
  const replyOffset = FeaManifestReply.endFeaManifestReply(builder);
  ServerReply.startServerReply(builder);
  ServerReply.addReplyTo(builder, CommandType.GET_FEA_MANIFEST);
  ServerReply.addFeaManifest(builder, replyOffset);
  return finishReply(builder, requestId, ServerReply.endServerReply(builder));
}

/** Answers a GET_FEA_BLOB the way the server does: the inclusive window when ranged, else the file. */
function blobReply(requestId: string, command: Message): ArrayBuffer {
  const request = command.server()!.feaBlobRequest()!;
  const ranged = request.ranged();
  const data = ranged ? FILE.slice(Number(request.start()), Number(request.end()) + 1) : FILE;
  const builder = new flatbuffers.Builder(512);
  const dataOffset = FeaBlobReply.createDataVector(builder, data);
  const nameOffset = builder.createString(request.filename() ?? "");
  FeaBlobReply.startFeaBlobReply(builder);
  FeaBlobReply.addFilename(builder, nameOffset);
  FeaBlobReply.addData(builder, dataOffset);
  FeaBlobReply.addRanged(builder, ranged);
  FeaBlobReply.addTotalSize(builder, BigInt(FILE.length));
  const replyOffset = FeaBlobReply.endFeaBlobReply(builder);
  ServerReply.startServerReply(builder);
  ServerReply.addReplyTo(builder, CommandType.GET_FEA_BLOB);
  ServerReply.addFeaBlob(builder, replyOffset);
  return finishReply(builder, requestId, ServerReply.endServerReply(builder));
}

function fakeServer() {
  const sent: Message[] = [];
  const correlator = new RequestCorrelator((payload) => {
    const command = parseMessage(payload.slice().buffer);
    sent.push(command);
    const id = requestIdOf(command);
    assert.ok(id, "command carries a request id");
    queueMicrotask(() => {
      const answer =
        command.commandType() === CommandType.GET_FEA_MANIFEST
          ? manifestReply(id!, command.server()!.feaManifestRequest()!.sourceKey()!, "bake-7", { fields: [] })
          : blobReply(id!, command);
      correlator.settle(parseMessage(answer));
    });
  });
  const transport = {
    request: (build, opts) => correlator.request(build, opts),
    getInstanceId: () => 99,
  } as Pick<Comms, "request" | "getInstanceId"> as Comms;
  return { sent, transport };
}

test("supports the two verbs the streaming loader needs", () => {
  const cap = new WSFeaCapability(fakeServer().transport);
  assert.equal(cap.transport, "ws");
  assert.equal(cap.supports("fetchManifest"), true);
  assert.equal(cap.supports("fieldFetchers"), true);
});

test("fetchManifest asks GET_FEA_MANIFEST for the source and parses the bake's manifest", async () => {
  const { sent, transport } = fakeServer();
  const cap = new WSFeaCapability(transport);

  const manifest = await cap.fetchManifest("ignored", "cantilever");

  assert.deepEqual(manifest, { fields: [] });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].commandType(), CommandType.GET_FEA_MANIFEST);
  assert.equal(sent[0].server()?.feaManifestRequest()?.sourceKey(), "cantilever");
});

test("the fetchers read whole files and byte ranges over GET_FEA_BLOB", async () => {
  const { sent, transport } = fakeServer();
  const cap = new WSFeaCapability(transport);
  const { fetcher, rangeFetcher } = cap.fieldFetchers("ignored", "cantilever");

  const whole = new Uint8Array(await fetcher("/fea.mesh.glb"));
  assert.deepEqual(whole, FILE);
  const blobRequest = sent[0].server()?.feaBlobRequest();
  assert.equal(sent[0].commandType(), CommandType.GET_FEA_BLOB);
  assert.equal(blobRequest?.filename(), "fea.mesh.glb", "a leading slash is not part of the name");
  assert.equal(blobRequest?.ranged(), false);

  const { buf, ranged } = await rangeFetcher("fea.U.bin", 4, 9);
  assert.equal(ranged, true);
  assert.deepEqual(new Uint8Array(buf), FILE.slice(4, 10), "the range is inclusive of its end");
  assert.equal(buf.byteLength, 6, "the bytes are copied out of the message, not a view of it");
});

test("the cache key follows the bake the manifest came from", async () => {
  const { transport } = fakeServer();
  const cap = new WSFeaCapability(transport);
  const before = cap.fieldFetchers("ignored", "cantilever").cacheKey;
  await cap.fetchManifest("ignored", "cantilever");
  const after = cap.fieldFetchers("ignored", "cantilever").cacheKey;

  assert.notEqual(before, after);
  assert.match(after, /bake-7/);
});
