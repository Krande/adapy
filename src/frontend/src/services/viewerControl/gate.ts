// When this tab may be driven: the server allows viewer control (ADA_VIEWER_CONTROL, told through
// /config.js) AND the user turned "External controls" on for this tab. Until both hold the tab
// registers nothing and opens no channel; turning the switch off closes it again.

import { runtime } from "@/runtime/config";
import { useExternalControlsStore } from "@/state/externalControlsStore";

/** Run `start` while external controls are on (it returns its stop); returns the unsubscribe,
 *  which also stops it. `serverAllows` is read once: it is the page's configuration. */
export function followExternalControls(
  start: () => () => void,
  serverAllows: () => boolean = runtime.viewerControl,
): () => void {
  if (!serverAllows()) return () => {};
  let stop: (() => void) | null = null;
  const apply = (enabled: boolean) => {
    if (enabled && !stop) stop = start();
    else if (!enabled && stop) {
      stop();
      stop = null;
    }
  };
  apply(useExternalControlsStore.getState().enabled);
  const off = useExternalControlsStore.subscribe((s, p) => {
    if (s.enabled !== p.enabled) apply(s.enabled);
  });
  return () => {
    off();
    apply(false);
  };
}
