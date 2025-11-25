import * as flatbuffers from 'flatbuffers';
import {Message} from '../../flatbuffers/wsock';
import {CommandType, TargetType} from '../../flatbuffers/commands';
import {webSocketAsyncHandler} from './websocket_connector_async';

export function requestServerInfo(): void {
    if (webSocketAsyncHandler.socket?.readyState !== WebSocket.OPEN) {
        console.warn('WebSocket is not connected, cannot request server info');
        return;
    }

    const builder = new flatbuffers.Builder(256);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketAsyncHandler.instance_id);
    Message.addCommandType(builder, CommandType.GET_SERVER_INFO);
    Message.addClientType(builder, TargetType.WEB);
    Message.addTargetGroup(builder, TargetType.SERVER);
    const messageOffset = Message.endMessage(builder);

    builder.finish(messageOffset);
    const bytes = builder.asUint8Array();

    webSocketAsyncHandler.sendMessage(bytes).catch((err) => {
        console.error('Error sending server info request:', err);
    });
}
