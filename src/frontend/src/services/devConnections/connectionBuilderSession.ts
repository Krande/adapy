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
  devConnectionsApi,
  type DevConnectionsState,
} from "@/services/api/devConnections";
import { effectiveInputs, useConnectionBuilderStore } from "@/state/connectionBuilderStore";
import { useModelState } from "@/state/modelState";
import { overlay_file_in_scene } from "@/utils/scene/handlers/overlay_file_in_scene";
import { unload_any_source } from "@/utils/scene/handlers/unload_any_source";

/** Scene source names of builder previews start with this. */
export const PREVIEW_SOURCE_PREFIX = "connection-preview:";
const INPUT_DEBOUNCE_MS = 350;

let started = false;
let requested = 0;
let debounce: ReturnType<typeof setTimeout> | null = null;

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
    if (seq !== requested) return;
    const sourceName = `${PREVIEW_SOURCE_PREFIX}${specName}#${seq}`;
    await overlay_file_in_scene(sourceName, result.key, { scope: result.scope });
    if (seq !== requested) {
      void unload_any_source(sourceName);
      return;
    }
    dropPreviews(sourceName);
    useConnectionBuilderStore.getState().setBuild({ status: "done", result });
  } catch (err) {
    if (seq !== requested) return;
    const traceback = err instanceof DevBuildError ? err.traceback : null;
    const error = err instanceof Error ? err.message : String(err);
    useConnectionBuilderStore.getState().setBuild({ status: "error", specName, error, traceback });
  }
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
