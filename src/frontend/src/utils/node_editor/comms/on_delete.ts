import * as flatbuffers from "flatbuffers";
import {Message} from "../../../flatbuffers/wsock/message";
import {webSocketAsyncHandler} from "../../websocket/websocket_connector_async";
import {CommandType} from "../../../flatbuffers/commands/command-type";
import {TargetType} from "../../../flatbuffers/commands/target-type";
import {Server} from "../../../flatbuffers/server/server";
import {FileObject} from "../../../flatbuffers/base";

export async function onDelete(elements: { nodes: any[], edges: any[] }) {
    if (elements.nodes.length > 0) {
        await on_delete_nodes(elements.nodes)
    }
    if (elements.edges.length > 0) {
        await on_delete_edges(elements.edges)
    }
}


async function on_delete_nodes(elements: any[]) {
    for (const element of elements) {
        if (element.type == "file_object") {
            await delete_file_object(element)
        }
        console.log("onDelete node", element)
    }
}

async function on_delete_edges(elements: any[]) {
    console.log("onDelete edges", elements)
}

async function delete_file_object(file_object: any) {
    console.log("Deleting file object", file_object)
    let builder = new flatbuffers.Builder(1024);
    let file_obj = file_object.data.fileobject;

    let filename = builder.createString(file_obj.name())
    let filepath = builder.createString(file_obj.filepath())

    FileObject.startFileObject(builder);
    FileObject.addName(builder, filename);
    FileObject.addFilepath(builder, filepath);
    FileObject.addFileType(builder, file_obj.fileType());
    FileObject.addGlbFile(builder, file_obj.glbFile());
    FileObject.addIfcsqliteFile(builder, file_obj.ifcsqliteFile());
    let file_obj_copy = FileObject.endFileObject(builder);

    Server.startServer(builder);
    Server.addDeleteFileObject(builder, file_obj_copy);
    let server_data = Server.endServer(builder);

    Message.startMessage(builder);
    Message.addServer(builder, server_data);
    Message.addInstanceId(builder, webSocketAsyncHandler.instance_id);
    Message.addCommandType(builder, CommandType.DELETE_FILE_OBJECT);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    builder.finish(Message.endMessage(builder));
    await webSocketAsyncHandler.sendMessage(builder.asUint8Array());
}