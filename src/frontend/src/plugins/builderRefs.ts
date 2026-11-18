// A plugin's rows shown as boxes among the cell builder's cells (`registerBuilderRefs`, plugin
// API 1.30.0): a docked UI's supports or modifications, say, in Topology > Input. The plugin
// projects its refs from the doc (`project`); the builder draws them, lists them in its model
// tree, picks them, and moves and resizes them with the cells' gizmos and snapping. Each edit
// is handed back (`setIncluded`, `rename`, `remove`, `write`), and the plugin writes its own doc
// key (`setDocExtra`), from which the refs are projected again. A gizmo drag is one undo step.
//
// The refs themselves live in the builder store (state/cellbuilder/refsSlice.ts): `refs`, by id.

import { useCellBuilderStore } from "@/state/cellBuilderStore";
import { addBuilderRefProvider, removeBuilderRefProvider, type BuilderRefProvider } from "@/state/cellbuilder/refsSlice";

export type {
  BuilderRef,
  BuilderRefEdit,
  BuilderRefProvider,
  BuilderRefSpec,
} from "@/state/cellbuilder/refsSlice";
export { builderRefId } from "@/state/cellbuilder/refsSlice";

/** Show `provider`'s refs in the builder; returns the unregister. Registering an owner again
 *  replaces its provider. */
export function registerBuilderRefs(provider: BuilderRefProvider): () => void {
  if (!provider.owner) {
    console.warn("[builder refs] provider registered without an owner -- ignored");
    return () => {};
  }
  const drop = addBuilderRefProvider(provider);
  useCellBuilderStore.getState().refreshRefs();
  return () => {
    if (drop()) useCellBuilderStore.getState().refreshRefs();
  };
}

/** Unregister the refs provider of `owner`, if any (`ctx.scene.remove(owner)`). */
export function releaseBuilderRefs(owner: string): void {
  if (removeBuilderRefProvider(owner)) useCellBuilderStore.getState().refreshRefs();
}
