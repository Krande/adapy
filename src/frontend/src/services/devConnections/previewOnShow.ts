// Which connection-builder preview is on show, and what goes with it when it leaves the scene.
//
// The preview's clash run lives BESIDE the model, not in it, so unloading the model alone leaves it
// floating over whatever comes next -- a replay, another file. Every change of the loaded sources is
// reported here (the session, and `clearScene` when the scene is emptied); a preview no longer
// loaded takes its clash with it. Its annotations and weld marks go by the same rule, kept beside
// what draws them (`connectionOverlay`, `connectionWeldMarks`).
//
// The name is kept after the preview leaves: a preview is named once per build, so the same name
// loaded again is the same joint -- a renderer switch loading every model again in the viewport
// switched to -- and the session draws its annotations again over it.
//
// Its own module, free of the session's loaders, so the rule is testable without a canvas.

import { useClashCheckStore } from "@/state/clashCheckStore";

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

/** The preview on show is no longer loaded: take its clash out of the scene too. */
export function forgetPreviewIfUnloaded(loaded: ReadonlySet<string>): void {
  if (!current || loaded.has(current)) return;
  const clash = useClashCheckStore.getState();
  if (clash.sourceKey?.startsWith(PREVIEW_SOURCE_PREFIX)) clash.reset();
}
