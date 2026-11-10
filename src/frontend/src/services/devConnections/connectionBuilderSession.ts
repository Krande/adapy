// The connection builder's live loop (`ada dev connections`):
//
//   * follow the served library's state (/api/dev/connections/events) into
//     the store, and keep a valid spec selected across reloads (a spec that
//     was renamed or removed falls back to the library's first);
//   * rebuild the selected spec whenever the library reloads, the selection
//     changes, or an input changes (debounced, so dragging a slider does not
//     queue a build per pixel);
//   * load each finished build into the scene, and only then drop the one it
//     replaces, so the view never flashes empty and the camera stays put.
//
// A build that finishes after a newer one was asked for is discarded.

import {
  DevBuildError,
  type DevBuildResult,
  devConnectionsApi,
  type DevConnectionsState,
} from "@/services/api/devConnections";
import type { ContactRunDoc } from "@/services/clash/contactRuns";
import { contactRunToResult, useClashCheckStore } from "@/state/clashCheckStore";
import { effectiveInputs, useConnectionBuilderStore } from "@/state/connectionBuilderStore";
import { useModelState } from "@/state/modelState";
import { selectInOtherModel } from "@/utils/scene/crossModelSelect";
import { overlay_file_in_scene } from "@/utils/scene/handlers/overlay_file_in_scene";
import { unload_any_source } from "@/utils/scene/handlers/unload_any_source";

/** Scene source names of builder previews start with this. */
export const PREVIEW_SOURCE_PREFIX = "connection-preview:";
const INPUT_DEBOUNCE_MS = 350;

let started = false;
let requested = 0;
let debounce: ReturnType<typeof setTimeout> | null = null;

// Loads wait for the 3D scene (see useConnectionBuilderBoot): the first build
// finishes before the canvas mounts on a cold start, and a model loaded then is
// lost. Capped, so a shell that never reports a scene still gets its loads.
const SCENE_WAIT_MAX_MS = 15000;
let markSceneReady: () => void = () => {};
const sceneReady = new Promise<void>((resolve) => {
  markSceneReady = resolve;
});

/** The viewer's scene is mounted: builds may load into it. */
export function markConnectionBuilderSceneReady(): void {
  markSceneReady();
}

function untilSceneReady(): Promise<void> {
  return Promise.race([sceneReady, new Promise<void>((resolve) => setTimeout(resolve, SCENE_WAIT_MAX_MS))]);
}

/** Start the loop once per page; later calls are no-ops. Returns nothing to stop: it lives with the page. */
export function startConnectionBuilderSession(): void {
  if (started) return;
  started = true;
  const store = useConnectionBuilderStore;

  devConnectionsApi.subscribe(
    (event) => {
      const prev = store.getState().library;
      store.getState().setConnected(true);
      store.getState().setLibrary(event);
      keepSelectionValid(event);
      const reloaded = prev === null || prev.generation !== event.generation;
      if (reloaded && event.status === "ready") requestBuild(0);
    },
    () => store.getState().setConnected(false),
  );

  // Selection and input edits rebuild; the library's own updates are handled above.
  store.subscribe((s, prev) => {
    if (s.selectedSpec !== prev.selectedSpec) requestBuild(0);
    else if (s.inputsBySpec !== prev.inputsBySpec) requestBuild(INPUT_DEBOUNCE_MS);
  });
}

function keepSelectionValid(state: DevConnectionsState): void {
  const s = useConnectionBuilderStore.getState();
  const names = state.specs.map((spec) => spec.name);
  if (s.selectedSpec && names.includes(s.selectedSpec)) return;
  if (state.status !== "ready" || names.length === 0) return;
  const first = state.specs.find((spec) => spec.in_library) ?? state.specs[0];
  s.selectSpec(first.name);
}

/** Rebuild the selected spec after `delayMs` (0: now); a newer request supersedes this one. */
export function requestBuild(delayMs: number): void {
  if (debounce) clearTimeout(debounce);
  debounce = setTimeout(() => {
    debounce = null;
    void buildSelected();
  }, delayMs);
}

async function buildSelected(): Promise<void> {
  const s = useConnectionBuilderStore.getState();
  const specName = s.selectedSpec;
  const spec = s.library?.specs.find((x) => x.name === specName);
  if (!specName || !spec || s.library?.status !== "ready") return;

  const seq = ++requested;
  s.setBuild({ status: "building", specName });
  try {
    const result = await devConnectionsApi.build(specName, effectiveInputs(spec.defaults, s.inputsBySpec[specName]));
    await untilSceneReady();
    if (seq !== requested) return;
    const sourceName = `${PREVIEW_SOURCE_PREFIX}${specName}#${seq}`;
    await overlay_file_in_scene(sourceName, result.key, { scope: result.scope });
    if (seq !== requested) {
      void unload_any_source(sourceName);
      return;
    }
    dropPreviews(sourceName);
    currentPreview = sourceName;
    showClash(result, sourceName);
    useConnectionBuilderStore.getState().setBuild({ status: "done", result });
  } catch (err) {
    if (seq !== requested) return;
    const traceback = err instanceof DevBuildError ? err.traceback : null;
    const declined = err instanceof DevBuildError && err.declined;
    const error = err instanceof Error ? err.message : String(err);
    useConnectionBuilderStore.getState().setBuild({ status: "error", specName, error, traceback, declined });
  }
}

/** The scene source name of the preview on show; what select-in-scene and the clash run name. */
let currentPreview: string | null = null;

/**
 * The joint's clash in Scene > Clashes: the build's convex-contact run, read as any run of the
 * loaded model is, so its row selects the members, and its patches, normals and incoming/landing
 * colours draw in 3D.
 *
 * The run measured exactly the GLB loaded under `sourceName` (its member ids are that GLB's
 * element ids), so it is filed under that source -- a run on a file matches the model loaded
 * under its `source_key`. The store is pointed at the same source, so the Clashes tab's
 * follow-the-loaded-model rule keeps the result instead of clearing it. Never stored: the next
 * build replaces it.
 */
function showClash(result: DevBuildResult, sourceName: string): void {
  const clash = useClashCheckStore.getState();
  if (!result.clash_run) {
    if (clash.sourceKey?.startsWith(PREVIEW_SOURCE_PREFIX)) clash.setSource(sourceName, sourceName);
    return;
  }
  const run: ContactRunDoc = {
    ...result.clash_run,
    target: { kind: "file", source_key: sourceName },
    geometry: { ...result.clash_run.geometry, key: sourceName },
  };
  clash.setSource(sourceName, sourceName);
  useClashCheckStore.setState({ result: contactRunToResult(run, null), runKey: null, derivedKey: null });
}

/** Select objects of the preview on show (members, welds) by name. */
export function selectPreviewObjects(names: string[]): void {
  if (!currentPreview) return;
  void selectInOtherModel({ file: currentPreview, nodeNames: names });
}

/** Unload every builder preview but `keep`. */
function dropPreviews(keep: string | null): void {
  for (const name of useModelState.getState().loadedSourceNames) {
    if (name.startsWith(PREVIEW_SOURCE_PREFIX) && name !== keep) void unload_any_source(name);
  }
}

/** Reload the library now (the watcher does this on save); the event stream reports the result. */
export async function reloadLibrary(): Promise<void> {
  await devConnectionsApi.reload();
}

/** A fresh library process, re-reading ada_connections.toml. */
export async function restartLibrary(): Promise<void> {
  await devConnectionsApi.restart();
}
