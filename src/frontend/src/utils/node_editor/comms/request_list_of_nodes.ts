import {Message} from '../../../flatbuffers/wsock'
import {CommandType} from '../../../flatbuffers/commands/command-type'
import {TargetType} from '../../../flatbuffers/commands/target-type'
import * as flatbuffers from "flatbuffers";
import {webSocketAsyncHandler} from "../../websocket_connector_async";

export async function request_list_of_nodes() {
    console.log('Querying server for mesh info');
    let builder = new flatbuffers.Builder(1024);
    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketAsyncHandler.instance_id);
    Message.addCommandType(builder, CommandType.LIST_PROCEDURES);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));
    await webSocketAsyncHandler.sendMessage(builder.asUint8Array());
}