// Putting the cursor on ONE joint, from wherever the user pointed at it.
//
// Three surfaces name the same joint -- a sphere in the 3D view, a row in the Clashes tab, an
// arrow key in the Joints tab -- and all three must end in the same state: the joint is the
// store's cursor, its group is open, its members are selected in the model, and the panel showing
// its detail is the one on screen. That is one function, not three nearly-identical handlers,
// because the difference between them is only where the id came from.

import type { ContactRunDoc } from "@/services/clash/contactRuns";
import { checkedSourceName, contactRunOf, useClashCheckStore } from "@/state/clashCheckStore";
import { loadedSourceGroups, useModelState } from "@/state/modelState";
import { contactSceneElements } from "@/utils/scene/clashContactPlan";
import { useSceneInfoStore } from "@/state/sceneInfoStore";
import { selectInOtherModel } from "@/utils/scene/crossModelSelect";

export interface FocusOptions {
  /** Bring the `Joints` tab up. True for a click in the 3D view (the panel may be closed, or on
   *  another tab, and a cursor nobody can see is not a selection); false when the click came FROM
   *  a panel, which is already the one on screen. */
  revealPanel?: boolean;
}

/** Focus `jointId`: store cursor, group opened, members selected, panel revealed if asked. */
export async function focusJoint(jointId: string, opts: FocusOptions = {}): Promise<void> {
  const store = useClashCheckStore.getState();
  store.focusJoint(jointId);

  if (opts.revealPanel) {
    const scenePanel = useSceneInfoStore.getState();
    scenePanel.setMode("joints");
    scenePanel.setShowSceneInfoBox(true);
  }

  const joint = store.result?.jointsById.get(jointId);
  // A convex-contact run names its members by model and ELEMENT ID (a saved group's pairs members of
  // different models; names repeat within one): selected by those, in whichever loaded models they are.
  const run = contactRunOf(store.result);
  // Names are the fallback for a one-model run whose model is loaded under another name.
  if (joint && run) {
    const n = await selectContactElements(run, [jointId], joint.members.map((m) => m.name).join(" + "));
    if (n > 0 || run.target.kind === "group") return;
  }
  const file = checkedSourceName(store.sourceName, useModelState.getState().loadedSourceName);
  if (!joint || !file) return;
  await selectInOtherModel({ file, nodeNames: joint.members.map((m) => m.name) });
}

/** Select the members of `jointIds` ("select all" on a group, "select members" on a joint): by
 *  model and element id for a saved group's convex-contact run, else `names` in `file`. */
export async function selectClashMembers(jointIds: readonly string[], file: string | null, names: readonly string[]): Promise<void> {
  const run = contactRunOf(useClashCheckStore.getState().result);
  if (run) {
    const n = await selectContactElements(run, jointIds, null);
    if (n > 0 || run.target.kind === "group") return;
  }
  if (!file) return;
  await selectInOtherModel({ file, nodeNames: [...names] });
}

/** Select contacts' members by (loaded source, element id). Imported on first use: it reaches the scene. */
async function selectContactElements(run: ContactRunDoc, jointIds: readonly string[], label: string | null): Promise<number> {
  const [{ selectSourceElements }, { useAssetBrowserStore }] = await Promise.all([
    import("@/utils/groups/groupScene"),
    import("@/state/assetBrowserStore"),
  ]);
  const refs = contactSceneElements(run, jointIds, loadedSourceGroups.keys(), useAssetBrowserStore.getState().loaded);
  if (refs.length === 0) return 0;
  return selectSourceElements(refs, label ?? `${refs.length} clash member(s)`);
}
