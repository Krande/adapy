import {Message} from "../../flatbuffers/wsock/message";
import {webSocketHandler} from "../websocket_connector";
import {CommandType} from "../../flatbuffers/wsock/command-type";
import {TargetType} from "../../flatbuffers/wsock/target-type";
import * as flatbuffers from "flatbuffers";
import {useNodeEditorStore} from "../../state/useNodeEditorStore";

export function start_new_node_editor(){

    let builder = new flatbuffers.Builder(1024);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketHandler.instance_id);
    Message.addCommandType(builder, CommandType.START_NEW_NODE_EDITOR);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));

    webSocketHandler.sendMessage(builder.asUint8Array());
    console.log('Starting new node editor');
    useNodeEditorStore.getState().setIsNodeEditorVisible(false);
}