import {FileObject, FileType} from "@/flatbuffers/base";
import * as flatbuffers from "flatbuffers";
import {Message} from "@/flatbuffers/wsock/message";
import {comms} from "@/utils/comms";
import {CommandType} from "@/flatbuffers/commands";
import {TargetType} from "@/flatbuffers/commands/target-type";
import {Server} from "@/flatbuffers/server/server";
// NOTE: convert_source_file and conversionStore are imported lazily
// inside the REST branch below. In WS / desktop mode (the embedded
// zip shipped with the Python package) they're never reached, and
// dynamic-import keeps them out of the main bundle that gets inlined
// into index.html — Vite emits them as separate chunks instead.

function isRestMode(): boolean {
    return (window as any).COMMS_MODE === "rest";
}

function convertEnabled(): boolean {
    return Boolean((window as any).CONVERT_ENABLED);
}

async function start_file_in_local_app(fileobject: FileObject) {
    console.log("start_file_in_local_app" + fileobject.name());
    let builder = new flatbuffers.Builder(1024);

    let file_name = builder.createString(fileobject.name());
    let file_path = builder.createString(fileobject.filepath());

    FileObject.startFileObject(builder);
    FileObject.addName(builder, file_name);
    FileObject.addFilepath(builder, file_path);
    let file_object = FileObject.endFileObject(builder);

    Server.startServer(builder);
    Server.addStartFileInLocalApp(builder, file_object);
    let serverStore = Server.endServer(builder);

    Message.startMessage(builder);
    Message.addInstanceId(builder, comms.getInstanceId());
    Message.addCommandType(builder, CommandType.START_FILE_IN_LOCAL_APP);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    Message.addServer(builder, serverStore);
    builder.finish(Message.endMessage(builder));

    await comms.sendCommand(builder.asUint8Array());
}

async function send_view_request(name: string) {
    let builder = new flatbuffers.Builder(1024);
    let get_file_name = builder.createString(name);

    Server.startServer(builder);
    Server.addGetFileObjectByName(builder, get_file_name);
    let serverStore = Server.endServer(builder);

    Message.startMessage(builder);
    Message.addInstanceId(builder, comms.getInstanceId());
    Message.addCommandType(builder, CommandType.VIEW_FILE_OBJECT);
    Message.addTargetGroup(builder, TargetType.SERVER);
    Message.addClientType(builder, TargetType.WEB);
    Message.addServer(builder, serverStore);
    builder.finish(Message.endMessage(builder));

    await comms.sendCommand(builder.asUint8Array());
}

export async function view_file_object_from_server(fileobject: FileObject) {
    const sourceName = fileobject.name() || "";
    console.log("get_file_object_from_server" + sourceName);

    // REST (hosted) mode: anything that isn't already GLB goes through
    // the server-side conversion pipeline. The backend serves the
    // derived blob via VIEW_FILE_OBJECT once /api/convert reports done.
    if (isRestMode()) {
        const isGlb = sourceName.toLowerCase().endsWith(".glb");
        if (isGlb) {
            await send_view_request(sourceName);
            return;
        }
        if (!convertEnabled()) {
            console.warn("non-GLB file but conversion is disabled on this deployment");
            return;
        }
        try {
            const {ensureConvertedGlb} = await import("./convert_source_file");
            await ensureConvertedGlb(sourceName);
            await send_view_request(sourceName);
        } catch (err) {
            console.error("conversion failed", err);
            const {useConversionStore} = await import("../../../state/conversionStore");
            useConversionStore.getState().setJob(`${sourceName}::glb`, {
                sourceKey: `${sourceName}::glb`,
                jobId: "",
                derivedKey: "",
                status: "error",
                progress: 0,
                stage: "error",
                error: err instanceof Error ? err.message : String(err),
                startedAt: Date.now(),
            });
        }
        return;
    }

    // Desktop (WS) mode: legacy flow — IFC has a sub-GLB attached;
    // anything else opens in the desktop app.
    if (fileobject.fileType() !== FileType.IFC) {
        await start_file_in_local_app(fileobject);
        return;
    }

    const glb_file = fileobject.glbFile();
    if (!glb_file) {
        console.log("No GLB file found in the file object");
        return;
    }
    await send_view_request(glb_file.name() || "");
}
