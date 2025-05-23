import {Message} from '../../../flatbuffers/wsock'
import {CommandType} from '../../../flatbuffers/commands/command-type';
import {TargetType} from '../../../flatbuffers/commands/target-type';
import {MeshInfo} from '../../../flatbuffers/meshes';
import * as flatbuffers from "flatbuffers";
import {webSocketAsyncHandler} from "../../websocket/websocket_connector_async";

export async function query_ws_server_mesh_info(mesh_name: string, face_index: number): Promise<void> {
    console.log('Querying server for mesh info');

    let builder = new flatbuffers.Builder(1024);
    let mesh_name_str = builder.createString(mesh_name);

    MeshInfo.startMeshInfo(builder);
    MeshInfo.addObjectName(builder, mesh_name_str);
    MeshInfo.addFaceIndex(builder, face_index);
    let mesh_info = MeshInfo.endMeshInfo(builder);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketAsyncHandler.instance_id);
    Message.addCommandType(builder, CommandType.MESH_INFO_CALLBACK);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    Message.addMeshInfo(builder, mesh_info);
    builder.finish(Message.endMessage(builder));
    await webSocketAsyncHandler.sendMessage(builder.asUint8Array());
}