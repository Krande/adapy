// Viewer control on the websocket envelope (the FlatBuffer `Message`; `ada.comms.viewer_wire` is the
// Python side): VIEWER_COMMAND comes in with the command or batch as JSON in `payloadJson` and its
// id in `requestId`; the viewer sends VIEWER_ANSWER (the id echoed) and VIEWER_STATE back.

import * as flatbuffers from "flatbuffers";

import { CommandType } from "@/flatbuffers/commands/command-type";
import { TargetType } from "@/flatbuffers/commands/target-type";
import { Message } from "@/flatbuffers/wsock/message";

import type { ViewerCommandPayload } from "./answer";

/** Where a viewer's message goes: the REST server's socket (`server`), or back to the local client
 *  that sent the command, through the desktop websocket server's relay (`local`, its instance id). */
export type ViewerReplyTarget = { kind: "server" } | { kind: "local"; instanceId: number };

function build(commandType: CommandType, body: unknown, opts: { requestId?: string; instanceId?: number; to: ViewerReplyTarget }): Uint8Array {
  const builder = new flatbuffers.Builder(1024);
  const payload = builder.createString(JSON.stringify(body));
  const requestId = opts.requestId !== undefined ? builder.createString(opts.requestId) : 0;
  Message.startMessage(builder);
  if (opts.instanceId !== undefined) Message.addInstanceId(builder, opts.instanceId);
  Message.addCommandType(builder, commandType);
  Message.addClientType(builder, TargetType.WEB);
  if (opts.to.kind === "local") {
    Message.addTargetGroup(builder, TargetType.LOCAL);
    Message.addTargetId(builder, opts.to.instanceId);
  } else {
    Message.addTargetGroup(builder, TargetType.SERVER);
  }
  if (requestId) Message.addRequestId(builder, requestId);
  Message.addPayloadJson(builder, payload);
  builder.finish(Message.endMessage(builder));
  return builder.asUint8Array();
}

export function answerBytes(requestId: string, answer: unknown, to: ViewerReplyTarget, instanceId?: number): Uint8Array {
  return build(CommandType.VIEWER_ANSWER, answer, { requestId, instanceId, to });
}

export function stateBytes(state: unknown, instanceId?: number): Uint8Array {
  return build(CommandType.VIEWER_STATE, state, { instanceId, to: { kind: "server" } });
}

export function parseBytes(data: ArrayBuffer | Uint8Array): Message {
  return Message.getRootAsMessage(new flatbuffers.ByteBuffer(data instanceof Uint8Array ? data : new Uint8Array(data)));
}

/** A VIEWER_COMMAND's id and payload; null for any other message, or one whose payload is not JSON. */
export function readViewerCommand(message: Message): { requestId: string; payload: ViewerCommandPayload } | null {
  if (message.commandType() !== CommandType.VIEWER_COMMAND) return null;
  const requestId = message.requestId();
  const raw = message.payloadJson();
  if (!requestId || !raw) return null;
  try {
    const payload = JSON.parse(raw) as ViewerCommandPayload;
    return typeof payload === "object" && payload !== null ? { requestId, payload } : null;
  } catch {
    return null;
  }
}
