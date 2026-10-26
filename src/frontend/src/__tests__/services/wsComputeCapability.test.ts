import assert from "node:assert/strict";
import { test } from "node:test";
import * as flatbuffers from "flatbuffers";

// CALL_KERNEL: the websocket viewer's compute, run by the Python process (no /wasm to load
// WebAssembly from on file://). The fake server here decodes the request exactly as
// ada.comms.msg_handling.kernel_calls does and answers with arrays, so the assertions are on
// what WSComputeCapability sends and how it rebuilds the result the beam-solid loader takes.

import { CommandType } from "@/flatbuffers/commands/command-type";
import { Message } from "@/flatbuffers/wsock/message";
import { ServerReply } from "@/flatbuffers/server/server-reply";
import { KernelReply } from "@/flatbuffers/server/kernel-reply";
import { Error as FbError } from "@/flatbuffers/base/error";
import { parseMessage, requestIdOf, RequestCorrelator } from "@/utils/comms/wsRequests";
import { packKernelArrays, unpackKernelArrays, WSComputeCapability } from "@/services/capabilities/ws_capabilities";
import type { Comms } from "@/utils/comms";

function reply(requestId: string, build: (b: flatbuffers.Builder) => number, command: number): ArrayBuffer {
  const builder = new flatbuffers.Builder(1024);
  const serverReplyOffset = build(builder);
  const idOffset = builder.createString(requestId);
  Message.startMessage(builder);
  Message.addCommandType(builder, command);
  Message.addServerReply(builder, serverReplyOffset);
  Message.addRequestId(builder, idOffset);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array().slice().buffer;
}

function fakeServer(answer: (fn: string, inputs: Record<string, ArrayLike<number>>) => Record<string, any> | string) {
  const sent: Message[] = [];
  const correlator = new RequestCorrelator((payload) => {
    const command = parseMessage(payload.slice().buffer);
    sent.push(command);
    const id = requestIdOf(command)!;
    const call = command.server()!.kernelCall()!;
    const args = JSON.parse(call.argsJson()!);
    const inputs = unpackKernelArrays(args.arrays, call.dataArray()!);
    const out = answer(call.function_()!, inputs);
    queueMicrotask(() => {
      if (typeof out === "string") {
        correlator.settle(
          parseMessage(
            reply(
              id,
              (b) => {
                const msg = b.createString(out);
                FbError.startError(b);
                FbError.addMessage(b, msg);
                const err = FbError.endError(b);
                ServerReply.startServerReply(b);
                ServerReply.addError(b, err);
                return ServerReply.endServerReply(b);
              },
              CommandType.ERROR,
            ),
          ),
        );
        return;
      }
      const { descriptors, data } = packKernelArrays(out);
      correlator.settle(
        parseMessage(
          reply(
            id,
            (b) => {
              const fn = b.createString(call.function_()!);
              const json = b.createString(JSON.stringify({ arrays: descriptors }));
              const dataOffset = KernelReply.createDataVector(b, data);
              KernelReply.startKernelReply(b);
              KernelReply.addFunction(b, fn);
              KernelReply.addResultJson(b, json);
              KernelReply.addData(b, dataOffset);
              const kr = KernelReply.endKernelReply(b);
              ServerReply.startServerReply(b);
              ServerReply.addReplyTo(b, CommandType.CALL_KERNEL);
              ServerReply.addKernel(b, kr);
              return ServerReply.endServerReply(b);
            },
            CommandType.SERVER_REPLY,
          ),
        ),
      );
    });
  });
  const transport = {
    request: (build, opts) => correlator.request(build, opts),
    getInstanceId: () => 7,
  } as Pick<Comms, "request" | "getInstanceId"> as Comms;
  return { sent, transport };
}

test("packed arrays round-trip, each at an 8-byte aligned offset", () => {
  const arrays = { a: new Uint8Array([1, 2, 3]), b: new Float64Array([1.5, -2]), c: new Uint32Array([9]) };
  const { descriptors, data } = packKernelArrays(arrays);
  assert.ok(descriptors.every((d) => d.offset % 8 === 0));
  const back = unpackKernelArrays(descriptors, data);
  assert.deepEqual(Array.from(back.a), [1, 2, 3]);
  assert.deepEqual(Array.from(back.b), [1.5, -2]);
  assert.ok(back.c instanceof Uint32Array);
});

test("expandBeamSolids sends the artefact and positions and rebuilds the expanded buffers", async () => {
  const compact = new Uint8Array([65, 70, 66, 83, 1, 0, 0, 0]).buffer; // only forwarded, never parsed here
  const positions = new Float32Array([0, 0, 0, 1, 0, 0]);
  const { sent, transport } = fakeServer((fn, inputs) => {
    assert.equal(fn, "expand_beam_solids");
    assert.deepEqual(Array.from(inputs.compact), Array.from(new Uint8Array(compact)));
    assert.deepEqual(Array.from(inputs.points), Array.from(positions));
    return {
      positions: new Float32Array([0, 0, 0, 1, 0, 0, 1, 1, 0]),
      indices: new Uint32Array([0, 1, 2]),
      node0: new Uint32Array([0, 0, 0]),
      node1: new Uint32Array([1, 1, 1]),
      t: new Float32Array([0, 1, 1]),
      range_label: new Uint32Array([42]),
      range_tri_start: new Uint32Array([0]),
      range_tri_count: new Uint32Array([1]),
    };
  });
  const cap = new WSComputeCapability(transport);

  assert.equal(cap.supports("expandBeamSolids"), true);
  const out = await cap.expandBeamSolids(compact, positions);

  assert.equal(sent[0].commandType(), CommandType.CALL_KERNEL);
  assert.equal(out.nVerts, 3);
  assert.deepEqual(Array.from(out.indices), [0, 1, 2]);
  assert.deepEqual(Array.from(out.elemLabel), [42]);
  assert.deepEqual(Array.from(out.t), [0, 1, 1]);
  assert.ok(out.positions instanceof Float32Array && out.node0 instanceof Uint32Array);
});

test("a kernel the server refuses rejects with the server's reason", async () => {
  const { transport } = fakeServer(() => "kernel 'expand_beam_solids' needs adacpp, which this Python environment cannot import");
  const cap = new WSComputeCapability(transport);
  await assert.rejects(cap.expandBeamSolids(new ArrayBuffer(8), new Float32Array(3)), /needs adacpp/);
});
