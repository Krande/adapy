import {Message} from "../../../flatbuffers/wsock/message";
import {comms} from "../../comms";
import {CommandType, TargetType} from "../../../flatbuffers/commands";
import * as flatbuffers from "flatbuffers";
import {useNodeEditorStore} from "../../../state/useNodeEditorStore";

export async function start_new_node_editor(){

    let builder = new flatbuffers.Builder(1024);

    Message.startMessage(builder);
    Message.addInstanceId(builder, comms.getInstanceId());
    Message.addCommandType(builder, CommandType.START_NEW_NODE_EDITOR);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));

    await comms.sendCommand(builder.asUint8Array());
    console.log('Starting new node editor');
    useNodeEditorStore.getState().setIsNodeEditorVisible(false);
}