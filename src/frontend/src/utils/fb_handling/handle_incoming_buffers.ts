import {CommandType, Message} from '../../flatbuffers/wsock'
import * as flatbuffers from "flatbuffers";
import {reply_ping} from "./reply_ping";
import {update_scene} from "../scene/update_scene";
import {receive_mesh_info_reply} from "../mesh_select/receive_mesh_info_reply";
import {update_list_of_procedures} from "../node_editor/update_list_of_procedures";

export const handleFlatbufferMessage = (buffer: ArrayBuffer) => {
    // Wrap ArrayBuffer into FlatBuffer ByteBuffer
    const byteBuffer = new flatbuffers.ByteBuffer(new Uint8Array(buffer));
    let message = Message.getRootAsMessage(byteBuffer);
    let command_type = message.commandType();

    if (command_type === CommandType.PING) {
        reply_ping(message);
    } else if (command_type === CommandType.UPDATE_SCENE) {
        update_scene(message);
        update_list_of_procedures(message);
    } else if (command_type === CommandType.MESH_INFO_REPLY) {
        receive_mesh_info_reply(message);
    } else if (command_type == CommandType.SERVER_REPLY) {
        if (message.serverReply()?.replyTo() === CommandType.LIST_PROCEDURES) {
            update_list_of_procedures(message);
        } else if (message.serverReply()?.replyTo() === CommandType.MESH_INFO_CALLBACK) {
            receive_mesh_info_reply(message);
        }
    }
    console.log('Flatbuffer message received');
    console.log('Instance ID:', message.instanceId());
    console.log('Command Type:', CommandType[message.commandType()]);
    console.log('File Object:', message.fileObject);
    console.log('Mesh Info:', message.meshInfo());
}

