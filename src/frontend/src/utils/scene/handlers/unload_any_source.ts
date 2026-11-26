// THE way to unload one loaded model, for every caller -- the Scene tree's menu, the Files and
// Sources tabs, plugins (`ctx.scene.unloadModel`), the cell builder. Nothing else calls
// `unload_source_from_scene` (a guard test holds this), with one exception: a compile/recompile
// showing its result ("replaceSource" in loadModel.ts) must stay narrow -- unload exactly this
// one source and nothing wider -- even for a source name that happens to look like a streaming-FEA
// result, which this router would instead route to a full `clearScene`. A caller that routed
// through here would skip the streaming-FEA teardown below for every OTHER name.
// (`clearScene` is the other operation: clear the scene.)
//
// Unload a loaded source by name, routing to the right teardown:
// streaming-FEA models were loaded via replace_model (scene-wide, no
// per-source group registered), so only the scene's clear (`clearScene`) tears them
// down properly (FEA session store + animation driver included);
// regular overlays unload per-source.

import {isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {clearScene} from "@/utils/scene/handlers/clear_scene";
import {unload_source_from_scene} from "@/utils/scene/handlers/unload_source_from_scene";

export async function unload_any_source(name: string): Promise<void> {
    if (isStreamingFEAResult(name)) await clearScene({reason: "unload streamed result", keepQueuedLoads: true, keepProcedural: true});
    else unload_source_from_scene(name);
}
