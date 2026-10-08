// THE way to unload one loaded model, for every caller -- the Scene tree's menu, the Files and
// Sources tabs, plugins (`ctx.scene.unloadModel`), the cell builder. Nothing else calls
// `unload_source_from_scene` (a guard test holds this): a caller that did would skip the
// streaming-FEA teardown below. (`clear_loaded_model` is the other operation: clear the scene.)
//
// Unload a loaded source by name, routing to the right teardown:
// streaming-FEA models were loaded via replace_model (scene-wide, no
// per-source group registered), so only clear_loaded_model tears them
// down properly (FEA session store + animation driver included);
// regular overlays unload per-source.

import {isStreamingFEAResult} from "@/utils/scene/fileKinds";
import {clear_loaded_model} from "@/utils/scene/handlers/clear_loaded_model";
import {unload_source_from_scene} from "@/utils/scene/handlers/unload_source_from_scene";

export async function unload_any_source(name: string): Promise<void> {
    if (isStreamingFEAResult(name)) await clear_loaded_model();
    else unload_source_from_scene(name);
}
