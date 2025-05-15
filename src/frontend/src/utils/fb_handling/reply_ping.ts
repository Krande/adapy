// reply_ping.ts
import { Message } from "../../flatbuffers/wsock/message";
import * as flatbuffers from "flatbuffers";
import { webSocketAsyncHandler } from "../websocket_connector_async";
import { CommandType } from "../../flatbuffers/commands/command-type";
import { TargetType } from "../../flatbuffers/commands/target-type";

export async function reply_ping(message: Message): Promise<void> {
  try {
    console.log("Received ping from server. Replying with FlatBuffer message");

    const builder = new flatbuffers.Builder(1024);
    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketAsyncHandler.instanceId);
    Message.addCommandType(builder, CommandType.PONG);
    Message.addTargetId(builder, message.instanceId());
    Message.addTargetGroup(builder, TargetType.LOCAL);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));

    const bytes = builder.asUint8Array();
    await webSocketAsyncHandler.sendMessage(bytes);
  } catch (err) {
    console.error("Error replying to ping:", err);
  }
}
