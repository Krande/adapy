// When this tab may be driven: the server allows viewer control (ADA_VIEWER_CONTROL, told through
// /config.js) AND external controls are on for this tab -- the user's switch, or, until they touch it,
// the server's mode ("auto" on, "opt-in" off). Until both hold the tab asks for no ticket and opens no
// socket; turning the switch off closes it again.

import { runtime, type ViewerControlMode } from "@/runtime/config";
import { externalControlsOn, useExternalControlsStore } from "@/state/externalControlsStore";

/** Run `start` while external controls are on (it returns its stop); returns the unsubscribe,
 *  which also stops it. `mode` is read once: it is the page's configuration. */
export function followExternalControls(
  start: () => () => void,
  mode: () => ViewerControlMode = runtime.viewerControlMode,
): () => void {
  const serverMode = mode();
  if (serverMode === "off") return () => {};
  let stop: (() => void) | null = null;
  const apply = (on: boolean) => {
    if (on && !stop) stop = start();
    else if (!on && stop) {
      stop();
      stop = null;
    }
  };
  apply(externalControlsOn(useExternalControlsStore.getState().chosen, serverMode));
  const off = useExternalControlsStore.subscribe((s, p) => {
    if (s.chosen !== p.chosen) apply(externalControlsOn(s.chosen, serverMode));
  });
  return () => {
    off();
    apply(false);
  };
}
