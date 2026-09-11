// The "open a local model" decision for the CellBuilderPanel's local-disk browser (ws/REST
// parity plan, step 6: LIST_PROCEDURAL_MODELS / LOAD_PROCEDURAL_MODEL).
//
// Pulled out of the panel component so the decision -- fetch, then `open()` a real session or
// `loadFromDoc()` a view-only one, depending on whether this transport can commit an edit back --
// is testable against a fake capability, without rendering React or a real websocket. The panel
// itself only wires this to `capabilities.procedural` and `useCellBuilderStore`'s public actions;
// it never reaches into the store's internals.

import { LOCAL_MODEL_SCOPE } from "@/services/capabilities";
import type { ProceduralDoc } from "@/services/viewerApi";

/** The one entry a local-disk model listing carries per model (mirrors
 * `services/capabilities/types.ts`'s `ProceduralModelEntry`; re-declared here rather than
 * imported so this module's dependency surface stays limited to what it actually reads). */
export interface LocalModelListing {
  modelId: string;
}

export interface LocalModelOpenDeps {
  /** `capabilities.procedural.fetchModel` (or a fake standing in for it in a test). */
  fetchModel: (source: {
    scope: string;
    modelId: string;
  }) => Promise<{ available: boolean; doc?: ProceduralDoc | null }>;
  /** `capabilities.procedural.canEdit` at call time. */
  canEdit: boolean;
  /** `useCellBuilderStore.getState().open`. */
  open: (modelId: string, name: string, revision: number, doc: ProceduralDoc) => void;
  /** `useCellBuilderStore.getState().loadFromDoc`. */
  loadFromDoc: (doc: ProceduralDoc) => void;
}

export type LocalModelOpenResult = { ok: true } | { ok: false; error: string };

/** Fetch `entry.modelId` (tagged with `LOCAL_MODEL_SCOPE`, the sentinel `currentScopePart()`
 * resolves to on this transport) and hand the document to the store's existing public
 * `open()`/`loadFromDoc()` path.
 *
 * `canEdit` decides which: a connected socket can commit an edit back, so it opens a real,
 * editable session (`open`, revision `0` -- the websocket transport's real concurrency token is
 * the content hash `commitModel` tracks itself, not this numeric revision -- see
 * `WSProceduralModelCapability.commitModel`'s docstring); disconnected, there is nowhere to commit
 * to and it loads view-only (`loadFromDoc`), exactly like an embedded GLB document. */
export async function openLocalModel(
  entry: LocalModelListing,
  deps: LocalModelOpenDeps,
): Promise<LocalModelOpenResult> {
  let result: { available: boolean; doc?: ProceduralDoc | null };
  try {
    result = await deps.fetchModel({ scope: LOCAL_MODEL_SCOPE, modelId: entry.modelId });
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) };
  }
  if (!result.available || !result.doc) {
    return { ok: false, error: `Could not load "${entry.modelId}" from local disk` };
  }
  if (deps.canEdit) {
    deps.open(entry.modelId, entry.modelId, 0, result.doc);
  } else {
    deps.loadFromDoc(result.doc);
  }
  return { ok: true };
}
