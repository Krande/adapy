import {CommandType, Message} from '../../flatbuffers/wsock'
import * as flatbuffers from "flatbuffers";
import {reply_ping} from "./reply_ping";
import {update_scene_from_message} from "../scene/comms/update_scene_from_message";
import {receive_mesh_info_reply} from "../mesh_select/comms/receive_mesh_info_reply";
import {update_nodes} from "../node_editor/update_nodes";
import {handle_finished_procedure} from "../node_editor/handle_finished_procedure";

export const handleFlatbufferMessage = (buffer: ArrayBuffer) => {
    // Wrap ArrayBuffer into FlatBuffer ByteBuffer
    const byteBuffer = new flatbuffers.ByteBuffer(new Uint8Array(buffer));
    let message = Message.getRootAsMessage(byteBuffer);
    let command_type = message.commandType();

    if (command_type === CommandType.PING) {
        reply_ping(message);
    } else if (command_type === CommandType.UPDATE_SCENE) {
        update_scene_from_message(message);
        update_nodes(message);
    } else if (command_type === CommandType.MESH_INFO_REPLY) {
        receive_mesh_info_reply(message);
    } else if (command_type == CommandType.SERVER_REPLY) {
        if (message.serverReply()?.replyTo() === CommandType.LIST_PROCEDURES) {
            update_nodes(message);
        } else if (message.serverReply()?.replyTo() === CommandType.MESH_INFO_CALLBACK) {
            receive_mesh_info_reply(message);
        } else if (message.serverReply()?.replyTo() === CommandType.VIEW_FILE_OBJECT) {
            console.log('VIEW_FILE_OBJECT Server Reply message received');
            update_scene_from_message(message);
        } else if (message.serverReply()?.replyTo() === CommandType.RUN_PROCEDURE) {
            console.log('LIST_MESHES Server Reply message received');
            handle_finished_procedure(message);
        } else {
            console.error('Unknown Server Reply message type received: ', message.serverReply()?.replyTo());
        }
    } else if (command_type == CommandType.ERROR) {
        console.error('Server Error message received');
        console.error('Server Error message:', message.serverReply()?.error()?.message());
    } else  {
        console.error('Unknown Flatbuffer message type received: ', CommandType[command_type]);
    }
    console.log('Flatbuffer message received');
    console.log('Instance ID:', message.instanceId());
    console.log('Command Type:', CommandType[message.commandType()]);
    console.log('Mesh Info:', message.meshInfo());
}

