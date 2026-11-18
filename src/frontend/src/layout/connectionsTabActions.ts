// Opening the Connections panel (`core:component-controls`) on one of its tabs.
//
// The Scene panel's Joints group -- the connection builder, Clashes, the joints review and the
// joint overlays -- is the Connections panel's now. Whatever opened one of those tabs by setting
// the Scene panel's mode and revealing that panel opens the Connections panel on the tab instead:
// the tab goes in the panel's tab store, and the panel is revealed like any other.
//
// The old modes are still accepted. An opener outside core (a plugin, a composed shell, a script
// on `useSceneInfoStore`) may still set `clashes`, `joints`, `jointOverlays` or `connection`:
// `redirectMovedSceneModes` sends each to its tab here and gives the Scene panel back the mode it
// had, so such an opener lands where the tab is rather than on a Scene panel without it.

import {
  BUILDER_TAB,
  CLASHES_TAB,
  JOINT_OVERLAYS_TAB,
  JOINTS_REVIEW_TAB,
  useConnectionBuilderTabStore,
  type ConnectionsCoreTab,
} from "@/state/connectionBuilderTabStore";
import { useSceneInfoStore, type SceneInfoMode } from "@/state/sceneInfoStore";

import { CORE_PANEL_IDS, reveal } from "./panelActions";
import { isMirroredWindow } from "./sceneActions";

/** The Scene panel's modes whose tabs moved to the Connections panel, and the tab each is there. */
export const SCENE_MODE_TO_CONNECTIONS_TAB: Readonly<Partial<Record<SceneInfoMode, ConnectionsCoreTab>>> = {
  connection: BUILDER_TAB,
  clashes: CLASHES_TAB,
  joints: JOINTS_REVIEW_TAB,
  jointOverlays: JOINT_OVERLAYS_TAB,
};

/** Show the Connections panel on `tab` (one of core's, or a plugin's panel id). Returns whether
 *  the panel could be revealed; the tab is picked either way, for when it is opened. */
export function openConnectionsTab(tab: string): boolean {
  useConnectionBuilderTabStore.getState().select(tab);
  return reveal(CORE_PANEL_IDS.componentControls);
}

/**
 * Send a Scene panel mode of a moved tab to the Connections panel, from now on: the tab is opened
 * there and the Scene panel keeps the mode it had (Model, if that was a moved one too). Returns
 * the unsubscribe. Not in a pop-out window: the Connections panel stays in the main window.
 */
export function redirectMovedSceneModes(): () => void {
  if (isMirroredWindow()) return () => {};
  const redirect = (mode: SceneInfoMode, before: SceneInfoMode) => {
    const tab = SCENE_MODE_TO_CONNECTIONS_TAB[mode];
    if (!tab) return;
    openConnectionsTab(tab);
    useSceneInfoStore.getState().setMode(SCENE_MODE_TO_CONNECTIONS_TAB[before] ? "info" : before);
  };
  // A mode set before this ran (an opener at boot) is sent too.
  const now = useSceneInfoStore.getState().mode;
  if (SCENE_MODE_TO_CONNECTIONS_TAB[now]) redirect(now, "info");
  return useSceneInfoStore.subscribe((s, prev) => {
    if (s.mode !== prev.mode) redirect(s.mode, prev.mode);
  });
}
