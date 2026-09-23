// Putting the cursor on ONE joint, from wherever the user pointed at it.
//
// Three surfaces name the same joint -- a sphere in the 3D view, a row in the Clashes tab, an
// arrow key in the Joints tab -- and all three must end in the same state: the joint is the
// store's cursor, its group is open, its members are selected in the model, and the panel showing
// its detail is the one on screen. That is one function, not three nearly-identical handlers,
// because the difference between them is only where the id came from.

import { checkedSourceName, useClashCheckStore } from "@/state/clashCheckStore";
import { useModelState } from "@/state/modelState";
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
  const file = checkedSourceName(store.sourceName, useModelState.getState().loadedSourceName);
  if (!joint || !file) return;
  await selectInOtherModel({ file, nodeNames: joint.members.map((m) => m.name) });
}
