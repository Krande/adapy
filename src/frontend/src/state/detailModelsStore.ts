// The scope's kept detail models (Connections > Review, "Detail models"): every detail run of a
// clash result or a convex-contact run is a model of its own on the server (`ada.comms.rest.
// clash_details`), listed here newest first, loaded back into the scene, or deleted.
//
// Loading one does what the end of a fresh detail run does: the GLB is overlaid as a source of its
// own (`overlay_file_in_scene`, the Clashes tab's post-job load) and its take-off joins the
// produced joints the Review tab lists (`clashCheckStore.producedJoints`).

import { create } from "zustand";

import { clashCheckApi, type WireDetailModel } from "@/services/api/clashCheck";
import { capabilities } from "@/services/capabilities";
import { mergeProducedJoints, parseDetailStats, useClashCheckStore } from "@/state/clashCheckStore";

/** Where the server keeps detail models. A source loaded from under it is a detail model, not a
 *  model a clash check should follow (`ClashesPanel`'s `followLoadedSource`). */
export const DETAIL_MODELS_PREFIX = "_clash/details/";

/** Whether a loaded source is a generated detail model: a kept one, or one from before they were
 *  kept (`_derived/clash/...`). */
export function isDetailModelKey(key: string | null | undefined): boolean {
  return !!key && (key.startsWith(DETAIL_MODELS_PREFIX) || /^_derived\/clash\/(contacts-)?detail\//.test(key));
}

/** The I/O the store does, swappable for its test. */
export interface DetailModelDeps {
  list(scope: string): Promise<readonly WireDetailModel[]>;
  remove(scope: string, detailId: string): Promise<void>;
  stats(scope: string, key: string): Promise<unknown>;
  overlay(scope: string, glbKey: string): Promise<void>;
}

const realDeps: DetailModelDeps = {
  // Only where models are served by key: a transport that cannot stream a stored GLB could neither
  // load one nor has a server that keeps them.
  list: (scope) => (capabilities.files.supports("blobUrl") ? clashCheckApi.listDetailModels(scope) : Promise.resolve([])),
  remove: (scope, id) => clashCheckApi.deleteDetailModel(scope, id),
  stats: (scope, key) => clashCheckApi.getDetailStats(scope, key),
  async overlay(scope, glbKey) {
    const [{ overlay_file_in_scene }, { requestRender }] = await Promise.all([
      import("@/utils/scene/handlers/overlay_file_in_scene"),
      import("@/state/perfStore"),
    ]);
    await overlay_file_in_scene(glbKey, glbKey, { scope });
    requestRender();
  },
};

interface DetailModelsState {
  /** The scope the list is of. */
  scope: string | null;
  models: readonly WireDetailModel[];
  /** The model being loaded or deleted. */
  busyId: string | null;
  error: string | null;
  deps: DetailModelDeps;
  /** List the scope's detail models. Best effort: a list that cannot be read is an empty one. */
  load: (scope: string) => Promise<void>;
  /** Load one into the scene, and its joints into the produced section. */
  open: (scope: string, model: WireDetailModel) => Promise<void>;
  /** Delete one (the caller asks first). */
  remove: (scope: string, detailId: string) => Promise<void>;
}

export const useDetailModelsStore = create<DetailModelsState>((set, get) => ({
  scope: null,
  models: [],
  busyId: null,
  error: null,
  deps: realDeps,

  load: async (scope) => {
    try {
      const models = await get().deps.list(scope);
      set({ scope, models });
    } catch {
      set({ scope, models: get().scope === scope ? get().models : [] });
    }
  },

  open: async (scope, model) => {
    if (get().busyId) return;
    set({ busyId: model.detail_id, error: null });
    try {
      await get().deps.overlay(scope, model.glb_key);
      let stats = parseDetailStats(null);
      try {
        stats = parseDetailStats(await get().deps.stats(scope, model.stats_key));
      } catch {
        // The geometry is loaded; a take-off that cannot be read leaves the produced list as it was.
      }
      useClashCheckStore.setState((s) => ({
        producedJoints: mergeProducedJoints(s.producedJoints, stats.joints),
        producedSkipped: [...s.producedSkipped, ...stats.skipped.filter((x) => !s.producedSkipped.includes(x))],
      }));
      set({ busyId: null });
    } catch (e) {
      set({ busyId: null, error: `Could not load ${model.label}: ${e instanceof Error ? e.message : String(e)}` });
    }
  },

  remove: async (scope, detailId) => {
    if (get().busyId) return;
    set({ busyId: detailId, error: null });
    try {
      await get().deps.remove(scope, detailId);
      set((s) => ({ busyId: null, models: s.models.filter((m) => m.detail_id !== detailId) }));
    } catch (e) {
      set({ busyId: null, error: `Could not delete it: ${e instanceof Error ? e.message : String(e)}` });
    }
  },
}));

/** What kind of thing a model was detailed from -- its label (the spec and the file, node or group's
 *  name) says which one. */
export function detailModelSourceKind(m: WireDetailModel): string {
  const target = typeof m.source.target?.kind === "string" ? (m.source.target.kind as string) : null;
  const what = target === "group" ? "group" : target === "node" ? "node" : "file";
  return m.source.kind === "run" ? `clash run on a ${what}` : `clash check of a ${what}`;
}

/** A model's line under its label: when, what it was detailed from, and what it holds. */
export function detailModelLine(m: WireDetailModel): string {
  const when = m.created_at.replace("T", " ").replace("Z", "");
  const parts = [when, detailModelSourceKind(m)];
  if (m.status === "done" && m.counts) {
    parts.push(`${m.counts.joints} joint${m.counts.joints === 1 ? "" : "s"}`);
    parts.push(`${m.counts.welds} weld${m.counts.welds === 1 ? "" : "s"}`);
    if (m.counts.skipped) parts.push(`${m.counts.skipped} skipped`);
  } else {
    parts.push(m.status === "error" ? "failed" : m.status);
  }
  return parts.join(" · ");
}
