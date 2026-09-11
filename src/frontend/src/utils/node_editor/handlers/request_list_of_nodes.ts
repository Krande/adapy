import {Message} from '@/flatbuffers/wsock'
import {CommandType} from '@/flatbuffers/commands/command-type'
import {TargetType} from '@/flatbuffers/commands/target-type'
import * as flatbuffers from "flatbuffers";
import {comms} from "@/utils/comms";
import type {Comms} from "@/utils/comms";
import {update_nodes} from "./update_nodes";

// Serialize a LIST_PROCEDURES command tagged with `requestId` so the server's
// reply can be matched back to the awaiting caller.
export function build_list_of_nodes_request(instanceId: number, requestId: string): Uint8Array {
    const builder = new flatbuffers.Builder(1024);
    const requestIdOffset = builder.createString(requestId);
    Message.startMessage(builder);
    Message.addInstanceId(builder, instanceId);
    Message.addCommandType(builder, CommandType.LIST_PROCEDURES);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    Message.addRequestId(builder, requestIdOffset);
    builder.finish(Message.endMessage(builder));
    return builder.asUint8Array();
}

// Ask the server for its procedures (and file objects) and resolve with the
// reply. This verb is awaited end to end: the reply is correlated by
// request_id, so it is consumed here rather than by the general dispatcher,
// and the node-editor store is updated from it before the promise resolves.
// `transport` is injectable for tests; the app uses the comms singleton.
export async function request_list_of_nodes(transport: Comms = comms): Promise<Message> {
    console.log('Querying server for mesh info');
    const message = await transport.request((requestId) =>
        build_list_of_nodes_request(transport.getInstanceId(), requestId),
    );
    await update_nodes(message);
    return message;
}
