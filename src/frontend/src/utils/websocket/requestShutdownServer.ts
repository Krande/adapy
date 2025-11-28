import * as flatbuffers from 'flatbuffers';
import {Message} from '../../flatbuffers/wsock';
import {CommandType, TargetType} from '../../flatbuffers/commands';
import {webSocketAsyncHandler} from './websocket_connector_async';

export function requestShutdownServer(): void {
    if (webSocketAsyncHandler.socket?.readyState !== WebSocket.OPEN) {
        console.warn('WebSocket is not connected, cannot request server shutdown');
        return;
    }

    const builder = new flatbuffers.Builder(256);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketAsyncHandler.instance_id);
    Message.addCommandType(builder, CommandType.SHUTDOWN_SERVER);
    Message.addClientType(builder, TargetType.WEB);
    Message.addTargetGroup(builder, TargetType.SERVER);
    const messageOffset = Message.endMessage(builder);

    builder.finish(messageOffset);
    const bytes = builder.asUint8Array();

    webSocketAsyncHandler.sendMessage(bytes).catch((err) => {
        console.error('Error sending shutdown server request:', err);
    });
}
