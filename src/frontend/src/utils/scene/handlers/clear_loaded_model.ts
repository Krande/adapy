// The scene's teardown under its older name, as plugins have it (`@/viewer-core/scene`). It is
// `clearScene` (./clear_scene), the one path every clear takes, keeping what this name never touched:
// the loads still queued and an open procedural model. Core calls `clearScene` itself.

import {clearScene} from "./clear_scene";

export function clear_loaded_model(): Promise<void> {
    return clearScene({reason: "clear_loaded_model", keepQueuedLoads: true, keepProcedural: true});
}
