import {FileObject} from "../../flatbuffers/wsock/file-object";
import * as flatbuffers from "flatbuffers";
import {Message} from "../../flatbuffers/wsock/message";
import {webSocketHandler} from "../websocket_connector";
import {CommandType} from "../../flatbuffers/wsock/command-type";
import {TargetType} from "../../flatbuffers/wsock/target-type";
import {Server} from "../../flatbuffers/wsock/server";
import {FileType} from "../../flatbuffers/wsock/file-type";


export function view_file_object_from_server(fileobject: FileObject) {
    console.log("get_file_object_from_server" + fileobject.name());
    let builder = new flatbuffers.Builder(1024);
    if (fileobject.fileType() !== FileType.IFC) {
        console.log("Currently only supports viewing IFC files from the node editor");
        return
    }

    let glb_file = fileobject.glbFile();
    if (!glb_file) {
        console.log("No GLB file found in the file object");
        return
    }

    let get_file_name = builder.createString(glb_file.name());

    Server.startServer(builder);
    Server.addGetFileObjectByName(builder, get_file_name);
    let serverStore = Server.endServer(builder);

    Message.startMessage(builder);
    Message.addInstanceId(builder, webSocketHandler.instance_id);
    Message.addCommandType(builder, CommandType.VIEW_FILE_OBJECT);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    Message.addServer(builder, serverStore);
    builder.finish(Message.endMessage(builder));

    webSocketHandler.sendMessage(builder.asUint8Array());
}