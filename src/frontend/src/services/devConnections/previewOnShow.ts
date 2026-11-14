// Which connection-builder preview is on show, and what goes with it when it leaves the scene.
//
// The preview's annotations (member axes, weld labels: `connectionOverlay`) and its clash run live
// BESIDE the model, not in it, so unloading the model alone leaves them floating over whatever comes
// next -- a replay, another file. The session reports every change of the loaded sources here; a
// preview no longer loaded takes them with it.
//
// Its own module, free of the session's loaders, so the rule is testable without a canvas.

import { useClashCheckStore } from "@/state/clashCheckStore";
import { clearConnectionOverlay } from "@/utils/scene/connectionOverlay";

/** Scene source names of builder previews start with this. */
export const PREVIEW_SOURCE_PREFIX = "connection-preview:";

let current: string | null = null;

/** The scene source name of the preview on show; what select-in-scene and the clash run name. */
export function previewOnShow(): string | null {
  return current;
}

export function setPreviewOnShow(name: string | null): void {
  current = name;
}

/** The preview on show is no longer loaded: take its overlay and its clash out of the scene too. */
export function forgetPreviewIfUnloaded(loaded: ReadonlySet<string>): void {
  if (!current || loaded.has(current)) return;
  current = null;
  clearConnectionOverlay();
  const clash = useClashCheckStore.getState();
  if (clash.sourceKey?.startsWith(PREVIEW_SOURCE_PREFIX)) clash.reset();
}
