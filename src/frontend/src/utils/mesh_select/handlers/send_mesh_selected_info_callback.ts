import {Message} from '@/flatbuffers/wsock'
import {CommandType} from '@/flatbuffers/commands/command-type';
import {TargetType} from '@/flatbuffers/commands/target-type';
import {MeshInfo} from '@/flatbuffers/meshes';
import * as flatbuffers from "flatbuffers";
import {comms} from "@/utils/comms";
import {runtime} from "@/runtime/config";

export async function query_ws_server_mesh_info(
    mesh_name: string,
    face_index: number,
    file_name?: string | null,
): Promise<void> {
    // MESH_INFO_CALLBACK is a websocket-server concept (the WS backend
    // looks the object metadata up from its cached file structures).
    // The REST backend has no handler for it and replies with a
    // "not supported in REST mode" error — pure log noise for a panel
    // that simply stays empty. Skip the round-trip under REST.
    if (runtime.isRestMode()) {
        return;
    }
    let builder = new flatbuffers.Builder(1024);
    let mesh_name_str = builder.createString(mesh_name);
    let file_name_str = (file_name ? builder.createString(file_name) : null);

    MeshInfo.startMeshInfo(builder);
    MeshInfo.addObjectName(builder, mesh_name_str);
    MeshInfo.addFaceIndex(builder, face_index);
    if (file_name_str !== null) {
        MeshInfo.addFileName(builder, file_name_str);
    }
    let mesh_info = MeshInfo.endMeshInfo(builder);

    Message.startMessage(builder);
    Message.addInstanceId(builder, comms.getInstanceId());
    Message.addCommandType(builder, CommandType.MESH_INFO_CALLBACK);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    Message.addMeshInfo(builder, mesh_info);
    builder.finish(Message.endMessage(builder));
    await comms.sendCommand(builder.asUint8Array());
}
