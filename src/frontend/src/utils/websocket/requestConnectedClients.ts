import * as flatbuffers from 'flatbuffers';
import {Message} from '@/flatbuffers/wsock';
import {CommandType, TargetType} from '@/flatbuffers/commands';
import {comms} from '../comms';

export function requestConnectedClients(): void {
    if (!comms.isConnected()) {
        console.warn('WebSocket is not connected, cannot request connected clients');
        return;
    }

    const builder = new flatbuffers.Builder(256);

    Message.startMessage(builder);
    Message.addInstanceId(builder, comms.getInstanceId());
    Message.addCommandType(builder, CommandType.LIST_WEB_CLIENTS);
    Message.addClientType(builder, TargetType.WEB);
    Message.addTargetGroup(builder, TargetType.SERVER);
    const messageOffset = Message.endMessage(builder);

    builder.finish(messageOffset);
    const bytes = builder.asUint8Array();

    comms.sendCommand(bytes).catch((err) => {
        console.error('Error sending connected clients request:', err);
    });
}
