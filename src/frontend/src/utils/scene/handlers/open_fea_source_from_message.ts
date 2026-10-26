// OPEN_FEA_SOURCE: `FEAResult.show()` registered a result bake with the local
// websocket server and asks the viewer to open it.
//
// The load itself is the same one the REST viewer runs when a result file is
// toggled in the storage browser (`load_fea_with_defaults`): the manifest, then
// the mesh, then the default field's first step. The websocket FEA capability
// is what reads the bake from the server instead of the REST blob store.

import type {Message} from "@/flatbuffers/wsock/message";

export async function open_fea_source_from_message(message: Message): Promise<void> {
    const sourceKey = message.server()?.openFeaSource()?.sourceKey();
    if (!sourceKey) {
        console.error("OPEN_FEA_SOURCE without a source key");
        return;
    }
    const {clearActiveFeaStreaming, load_fea_with_defaults} = await import("./load_fea_streaming");
    // A result shown again under the same name is a new bake: drop the open
    // session, which would otherwise keep its mesh for a source it believes it
    // already holds.
    clearActiveFeaStreaming();
    await load_fea_with_defaults(sourceKey);
}
