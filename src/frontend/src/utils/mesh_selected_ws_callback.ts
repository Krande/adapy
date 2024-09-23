import {CommandType, Message, MeshInfo} from '../flatbuffers/wsock'
import * as flatbuffers from "flatbuffers";
import {webSocketHandler} from "./websocket_connector";

export const query_ws_server_mesh_info = (mesh_name: string, face_index: number) => {
    console.log('Querying server for mesh info');
    let builder = new flatbuffers.Builder(1024);
    let target_group = builder.createString("server");
    let client_type = builder.createString("web");
    let mesh_name_str = builder.createString(mesh_name);

    MeshInfo.startMeshInfo(builder);
    MeshInfo.addObjectName(builder, mesh_name_str);
    MeshInfo.addFaceIndex(builder, face_index);
    let mesh_info = MeshInfo.endMeshInfo(builder);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketHandler.instance_id);
    Message.addCommandType(builder, CommandType.MESH_INFO_CALLBACK);
    Message.addTargetGroup(builder, target_group);
    Message.addClientType(builder, client_type);
    Message.addMeshInfo(builder, mesh_info);
    builder.finish(Message.endMessage(builder));
    webSocketHandler.sendMessage(builder.asUint8Array());
}