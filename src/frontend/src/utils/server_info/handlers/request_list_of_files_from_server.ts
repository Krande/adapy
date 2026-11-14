import {Message} from "@/flatbuffers/wsock/message";
import {comms} from "@/utils/comms";
import * as flatbuffers from "flatbuffers";
import {CommandType, TargetType} from "@/flatbuffers/commands";
import {type ScopeOption, useScopeStore} from "@/state/scopeStore";

export const fileListScopeKey = (s: ScopeOption | null): string => (s ? `${s.kind}:${s.id ?? ""}` : "");

let listedFor: string | null = null;

/** The scope the last file-list request was sent for (the transport lists the current one). */
export const fileListRequestedFor = (): string | null => listedFor;

export async function request_list_of_files_from_server() {
    console.log('Querying server for list of files');
    listedFor = fileListScopeKey(useScopeStore.getState().current);
    let builder = new flatbuffers.Builder(1024);
    Message.startMessage(builder);
    Message.addInstanceId(builder, comms.getInstanceId());
    Message.addCommandType(builder, CommandType.LIST_FILE_OBJECTS);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));
    await comms.sendCommand(builder.asUint8Array());
}
