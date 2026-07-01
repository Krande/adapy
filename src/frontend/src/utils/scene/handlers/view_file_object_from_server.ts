import {FileObject, FileType} from "@/flatbuffers/base";
import * as flatbuffers from "flatbuffers";
import {Message} from "@/flatbuffers/wsock/message";
import {comms} from "@/utils/comms";
import {CommandType} from "@/flatbuffers/commands";
import {TargetType} from "@/flatbuffers/commands/target-type";
import {Server} from "@/flatbuffers/server/server";
import {runtime} from "@/runtime/config";
import {useModelState} from "@/state/modelState";
// NOTE: the conversion service and the conversionStore are imported
// lazily inside the REST branch below. In WS / desktop mode (the
// embedded zip shipped with the Python package) they're never
// reached, and dynamic-import keeps them out of the main bundle that
// gets inlined into index.html — Vite emits them as separate chunks
// instead.

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

/** REST-mode GLB load: fetch the model from its object-store URL and stream it into the
 * scene, instead of round-tripping the bytes through a VIEW_FILE_OBJECT /rpc response.
 *
 * Tries a presigned, server-relay-free, Range-capable URL first; on any failure (local
 * backends 503 the presign, or the object lacks the Content-Encoding metadata the browser
 * needs to auto-decompress) it falls back to the authed streaming ``/blobs/{key}`` GET,
 * where the server reliably forwards ``Content-Encoding: gzip``. Either way GLTFLoader
 * streams + the browser decompresses natively — no whole-file server buffer, no pako. */
export async function load_glb_by_url_rest(scope: string, glbKey: string, sourceName: string) {
    const {viewerApi} = await import("@/services/viewerApi");
    const {getAccessToken} = await import("@/services/auth/oidc");
    const {replace_model} = await import("./update_scene_from_message");
    const {beginLoadMetrics} = await import("@/utils/scene/loadMetrics");

    // Admin-only opt-in: time this load phase-by-phase (no-op / null when
    // collection is off or the user isn't an admin → zero extra cost).
    const metrics = beginLoadMetrics({scope, key: glbKey, sourceName, transport: "unknown"});

    let group: Awaited<ReturnType<typeof replace_model>> | undefined;
    try {
        try {
            const presigned = await viewerApi.requestDownloadUrl(scope as any, glbKey);
            metrics?.setTransport("presigned");
            group = await replace_model(presigned.url, undefined, sourceName, false, undefined, metrics);
        } catch (e) {
            console.warn("view: presigned GLB load failed, falling back to authed streaming GET", e);
            const url = viewerApi.blobUrl(scope as any, glbKey);
            const token = getAccessToken();
            const headers = token ? {Authorization: `Bearer ${token}`} : undefined;
            metrics?.setTransport("relayed");
            group = await replace_model(url, undefined, sourceName, false, headers, metrics);
        }
    } catch (e) {
        // Record the failed load too, then re-throw to the caller's handler.
        metrics?.fail(e instanceof Error ? e.message : String(e));
        throw e;
    }
    if (group && sourceName) {
        useModelState.getState().registerLoadedSource(sourceName, group);
    }
    useModelState.getState().setLoadedSourceName(sourceName);
}

export async function view_file_object_from_server(fileobject: FileObject) {
    const sourceName = fileobject.name() || "";
    console.log("get_file_object_from_server" + sourceName);

    // REST (hosted) mode: load the GLB straight from its object-store URL instead of
    // embedding the bytes in the VIEW_FILE_OBJECT /rpc response. This streams from storage
    // (presigned-direct when available), lets the browser decompress Content-Encoding: gzip
    // natively (no main-thread pako), and never buffers the whole model server-side.
    // Non-GLB sources are converted server-side first (the derived blob lands at
    // derivedKeyForGlb(source)).
    if (runtime.isRestMode()) {
        const {scopeUrlPart, useScopeStore} = await import("@/state/scopeStore");
        const {derivedKeyForGlb} = await import("./overlay_file_in_scene");
        const scope = scopeUrlPart(useScopeStore.getState().current);
        const isGlb = sourceName.toLowerCase().endsWith(".glb");
        try {
            if (!isGlb) {
                if (!runtime.convertEnabled()) {
                    console.warn("non-GLB file but conversion is disabled on this deployment");
                    return;
                }
                const {ensureConvertedGlb} = await import("@/services/conversion");
                await ensureConvertedGlb(scope, sourceName);
            }
            await load_glb_by_url_rest(scope, derivedKeyForGlb(sourceName), sourceName);
        } catch (err) {
            console.error("conversion/load failed", err);
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
