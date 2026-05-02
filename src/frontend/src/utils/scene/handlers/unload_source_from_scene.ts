// Remove a single previously-overlaid source from the scene without
// touching the rest. Counterpart to overlay_file_in_scene; called
// when the user unchecks a file in the StorageBrowser.

import {useModelState} from "@/state/modelState";
import {sceneRef} from "@/state/refs";

export function unload_source_from_scene(sourceName: string): void {
    const group = useModelState.getState().unregisterLoadedSource(sourceName);
    if (group) {
        // Mirror what clear_loaded_model does per-group: detach
        // children + remove from the parent scene so threejs can
        // free the GPU buffers on the next frame.
        group.clear();
        sceneRef.current?.remove(group);
    }
}
