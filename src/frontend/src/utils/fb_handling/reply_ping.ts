import {Message} from "../../flatbuffers/wsock/message";
import * as flatbuffers from "flatbuffers";
import {webSocketHandler} from "../websocket_connector";
import {CommandType} from "../../flatbuffers/wsock/command-type";
import {TargetType} from "../../flatbuffers/wsock/target-type";

export const reply_ping = (message: Message) => {
    console.log('Received ping from server. Replying with flatbuffer message');
    let builder = new flatbuffers.Builder(1024);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketHandler.instance_id);
    Message.addCommandType(builder, CommandType.PONG);
    Message.addTargetId(builder, message.instanceId());
    Message.addTargetGroup(builder, TargetType.LOCAL);
    Message.addClientType(builder, TargetType.WEB);

    builder.finish(Message.endMessage(builder));
    webSocketHandler.sendMessage(builder.asUint8Array());
}