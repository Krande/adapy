import {Message} from "../../../flatbuffers/wsock/message";
import {webSocketAsyncHandler} from "../../websocket_connector_async";
import * as flatbuffers from "flatbuffers";
import {CommandType, TargetType} from "../../../flatbuffers/commands";


export async function request_list_of_files_from_server() {
    console.log('Querying server for list of files');
    let builder = new flatbuffers.Builder(1024);
    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketAsyncHandler.instance_id);
    Message.addCommandType(builder, CommandType.LIST_FILE_OBJECTS);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));
    await webSocketAsyncHandler.sendMessage(builder.asUint8Array());
}