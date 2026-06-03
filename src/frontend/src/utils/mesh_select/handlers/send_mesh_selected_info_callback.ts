import {Message} from '@/flatbuffers/wsock'
import {CommandType} from '@/flatbuffers/commands/command-type';
import {TargetType} from '@/flatbuffers/commands/target-type';
import {MeshInfo} from '@/flatbuffers/meshes';
import * as flatbuffers from "flatbuffers";
import {comms} from "@/utils/comms";

export async function query_ws_server_mesh_info(
    mesh_name: string,
    face_index: number,
    file_name?: string | null,
): Promise<void> {
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
