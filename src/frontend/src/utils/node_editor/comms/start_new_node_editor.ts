import {Message} from "../../../flatbuffers/wsock/message";
import {webSocketAsyncHandler} from "../../websocket/websocket_connector_async";
import {CommandType, TargetType} from "../../../flatbuffers/commands";
import * as flatbuffers from "flatbuffers";
import {useNodeEditorStore} from "../../../state/useNodeEditorStore";

export async function start_new_node_editor(){

    let builder = new flatbuffers.Builder(1024);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketAsyncHandler.instance_id);
    Message.addCommandType(builder, CommandType.START_NEW_NODE_EDITOR);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));

    await webSocketAsyncHandler.sendMessage(builder.asUint8Array());
    console.log('Starting new node editor');
    useNodeEditorStore.getState().setIsNodeEditorVisible(false);
}