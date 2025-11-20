// reply_ping.ts
import { Message } from "../../flatbuffers/wsock/message";
import * as flatbuffers from "flatbuffers";
import type { AsyncWebSocketHandler } from "../websocket/websocket_connector_async";
import { CommandType } from "../../flatbuffers/commands/command-type";
import { TargetType } from "../../flatbuffers/commands/target-type";

export async function send_heartbeat(handler: AsyncWebSocketHandler): Promise<void> {
  try {
    const builder = new flatbuffers.Builder(1024);
    Message.startMessage(builder);
    Message.addInstanceId(builder, handler.instance_id);
    Message.addCommandType(builder, CommandType.HEARTBEAT);
    Message.addTargetId(builder, 0);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));

    const bytes = builder.asUint8Array();
    await handler.sendMessage(bytes);
  } catch (err) {
    console.error("Error sending heartbeat:", err);
  }
}
