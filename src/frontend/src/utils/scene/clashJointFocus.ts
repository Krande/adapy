// Putting the cursor on ONE joint, from wherever the user pointed at it.
//
// Three surfaces name the same joint -- a sphere in the 3D view, a row in the Clashes tab, an
// arrow key in the Review tab -- and all three must end in the same state: the joint is the
// store's cursor, its group is open, its members are selected in the model, and the panel showing
// its detail is the one on screen. That is one function, not three nearly-identical handlers,
// because the difference between them is only where the id came from.

import type { ContactRunDoc } from "@/services/clash/contactRuns";
import { checkedSourceName, contactRunOf, useClashCheckStore } from "@/state/clashCheckStore";
import { useModelState } from "@/state/modelState";
import { contactSceneElements } from "@/utils/scene/clashContactPlan";
import { JOINTS_REVIEW_TAB } from "@/state/connectionBuilderTabStore";
import { openConnectionsTab } from "@/layout/connectionsTabActions";
import { CORE_PANEL_IDS, reveal } from "@/layout/panelActions";
import { useClashContactPickStore } from "@/state/clashContactPickStore";
import { selectInOtherModel } from "@/utils/scene/crossModelSelect";

export interface FocusOptions {
  /** Bring the joints review up (the Connections panel's Review tab) and the Selected Object panel
   *  with it: for a deliberate "show it in Connections" (a viewer-control command). False when the
   *  focus came FROM a panel, which is already the one on screen. */
  revealPanel?: boolean;
  /** The joint was clicked in the 3D view (a marker or a contact patch). It is selected like any
   *  clicked object: its members are selected and the Selected Object panel describes the contact,
   *  if that panel is open. No panel is opened, and the Connections panel keeps its tab: when it is
   *  open, the tab on screen scrolls to the joint and highlights it (the store's cursor). */
  fromView?: boolean;
}

/** Focus `jointId`: store cursor, group opened, members selected, panel revealed if asked. */
export async function focusJoint(jointId: string, opts: FocusOptions = {}): Promise<void> {
  const store = useClashCheckStore.getState();
  // The cursor: whichever Connections tab lists the joint (Clashes, Review) opens its group,
  // scrolls to it and highlights it. Nothing on screen changes when no such tab is.
  store.focusJoint(jointId);

  // A contact pointed at in the 3D view is described in the Selected Object panel (its Contact
  // section, `ClashContactSection`), opened at the contact with its detail open (`clashContactPickStore`).
  if (opts.revealPanel || opts.fromView) useClashContactPickStore.getState().pick(jointId);
  if (opts.revealPanel) {
    openConnectionsTab(JOINTS_REVIEW_TAB);
    reveal(CORE_PANEL_IDS.objectInfo);
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
  // The loaded models as the model store lists them, which both viewports keep (the three.js scene's
  // own groups are only the three.js viewport's).
  const refs = contactSceneElements(run, jointIds, useModelState.getState().loadedSourceNames, useAssetBrowserStore.getState().loaded);
  if (refs.length === 0) return 0;
  return selectSourceElements(refs, label ?? `${refs.length} clash member(s)`);
}
