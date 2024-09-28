import {CommandType, MeshInfo, Message, TargetType} from '../../flatbuffers/wsock'
import * as flatbuffers from "flatbuffers";
import {webSocketHandler} from "../websocket_connector";

export const request_list_of_procedures = () => {
    console.log('Querying server for mesh info');
    let builder = new flatbuffers.Builder(1024);
    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketHandler.instance_id);
    Message.addCommandType(builder, CommandType.LIST_PROCEDURES);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));
    webSocketHandler.sendMessage(builder.asUint8Array());
}