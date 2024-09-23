import {CommandType, Message, SceneOperations} from '../flatbuffers/wsock'
import * as flatbuffers from "flatbuffers";
import {webSocketHandler} from "./websocket_connector";
import {useModelStore} from "../state/modelStore";

export const handleFlatbufferMessage = (buffer: ArrayBuffer) => {
    // Wrap ArrayBuffer into FlatBuffer ByteBuffer
    const byteBuffer = new flatbuffers.ByteBuffer(new Uint8Array(buffer));
    let message = Message.getRootAsMessage(byteBuffer);
    let command_type = message.commandType();

    if (command_type === CommandType.PING) {
        handle_ping(message);
    } else if (command_type === CommandType.UPDATE_SCENE) {
        handle_update_scene(message);
    }
    console.log('Flatbuffer message received');
    console.log('Instance ID:', message.instanceId());
    console.log('Command Type:', CommandType[message.commandType()]);
    console.log('File Object:', message.fileObject);
    console.log('Mesh Info:', message.meshInfo());
}

const handle_ping = (message: Message) => {
    console.log('Received ping from server. Replying with flatbuffer message');
    let builder = new flatbuffers.Builder(1024);

    let target_group = builder.createString("local");
    let client_type = builder.createString("web");

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketHandler.instance_id);
    Message.addCommandType(builder, CommandType.PONG);
    Message.addTargetId(builder, message.instanceId());
    Message.addTargetGroup(builder, target_group);
    Message.addClientType(builder, client_type);

    builder.finish(Message.endMessage(builder));
    webSocketHandler.sendMessage(builder.asUint8Array());
}

const handle_update_scene = (message: Message) => {
    console.log('Received scene update message from server');
    console.log('Scene Operation:', SceneOperations[message.sceneOperation()]);
    let fileObject = message.fileObject();
        if (!fileObject) {
        console.error("No file object found in the message");
        return;
    }
        // Get the filedata array (this is typically a Uint8Array)
    let data = fileObject.filedataArray();

    if (!data) {
        console.error("No filedata found in the file object");
        return;
    }

    const blob = new Blob([data], {type: 'model/gltf-binary'});
    const url = URL.createObjectURL(blob);

    useModelStore.getState().setModelUrl(url, null, ""); // Set the URL for the model
}