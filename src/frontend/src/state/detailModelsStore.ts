// The scope's kept detail models (Connections > Review, "Detail models"): every detail run of a
// clash result or a convex-contact run is a model of its own on the server (`ada.comms.rest.
// clash_details`), listed here newest first, loaded back into the scene, or deleted.
//
// Loading one puts back what the check and the detail run left on screen, as a SCENE STATE
// (`utils/scene/sceneRecord`) applied through the one restore path saved states use too
// (`utils/scene/applySceneState`): the elements that were checked (as a saved group's members are
// loaded), the check's target and result on the Clashes tab with its markers, and the model itself --
// overlaid as a source of its own, its take-off joining the produced joints the Review tab lists
// (`clashCheckStore.producedJoints`), the active detail model. What cannot be brought back is said,
// and the model loads regardless.

import { create } from "zustand";

import { clashCheckApi, type WireDetailModel } from "@/services/api/clashCheck";
import { capabilities } from "@/services/capabilities";
import { contactRunsApi } from "@/services/clash/contactRuns";
import { parseDetailStats, type DetailStats } from "@/state/clashCheckStore";
import { detailContext, type DetailContext } from "@/state/clashCheck/detailContext";
import type { ApplyOptions, ApplyOutcome } from "@/utils/scene/applySceneState";
import type { SceneRecord } from "@/utils/scene/sceneRecord";

/** A model's "Not built" report, read from its take-off when it is first opened. */
export type DetailReport =
  | { readonly status: "loading" }
  | { readonly status: "ready"; readonly stats: DetailStats }
  | { readonly status: "error"; readonly error: string };

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
  /** The check the model was detailed from (`state/clashCheck/detailContext`). */
  context(scope: string, model: WireDetailModel): Promise<DetailContext>;
  /** Put a scene state back (`utils/scene/applySceneState`). */
  apply(scope: string, state: SceneRecord, opts?: ApplyOptions): Promise<ApplyOutcome>;
}

const realDeps: DetailModelDeps = {
  // Only where models are served by key: a transport that cannot stream a stored GLB could neither
  // load one nor has a server that keeps them.
  list: (scope) => (capabilities.files.supports("blobUrl") ? clashCheckApi.listDetailModels(scope) : Promise.resolve([])),
  remove: (scope, id) => clashCheckApi.deleteDetailModel(scope, id),
  stats: (scope, key) => clashCheckApi.getDetailStats(scope, key),
  async apply(scope, state, opts) {
    const { applySceneStateLive } = await import("@/utils/scene/sceneStateLive");
    return applySceneStateLive(scope, state, opts);
  },
  context: (scope, model) =>
    detailContext(
      {
        readResult: (s, key) => clashCheckApi.getClashResult(s, key),
        readRun: (s, key) => contactRunsApi.getRun(s, key),
        async findGroup(s, by) {
          const { useSavedGroupsStore } = await import("@/state/savedGroupsStore");
          const pick = () => {
            const held = useSavedGroupsStore.getState();
            if (held.scope !== s) return undefined;
            return held.groups.find((g) => (by.id ? g.id === by.id : g.name === by.name));
          };
          const known = pick();
          if (known) return known;
          await useSavedGroupsStore.getState().load(s);
          return pick() ?? null;
        },
      },
      scope,
      model,
    ),
};

/** The scene state a detail model is loaded as: the check it came from (`ctx`, when it could be
 *  read) -- its elements and its result -- and the model as the active detail. No camera: the
 *  scene is not moved. */
export function detailModelState(model: WireDetailModel, ctx: DetailContext | null): SceneRecord {
  return {
    v: 1,
    models: [],
    camera: null,
    ...(ctx?.members.length ? { members: [...ctx.members] } : {}),
    ...(ctx
      ? { clash: { target: ctx.target, ...(ctx.runKey ? { runKey: ctx.runKey } : { resultKey: ctx.derivedKey ?? "" }) } }
      : {}),
    detail: { glbKey: model.glb_key, statsKey: model.stats_key, spec: model.spec },
  };
}

interface DetailModelsState {
  /** The scope the list is of. */
  scope: string | null;
  models: readonly WireDetailModel[];
  /** The model being loaded or deleted. */
  busyId: string | null;
  error: string | null;
  /** What the last load could not bring back of the check it came from (the model itself loaded). */
  notes: readonly string[];
  /** The model whose "Not built" report is open (one at a time). */
  reportId: string | null;
  /** Reports read so far, by detail id: a take-off does not change once written. */
  reports: Readonly<Record<string, DetailReport>>;
  deps: DetailModelDeps;
  /** Open (reading its take-off the first time) or close a model's "Not built" report. */
  toggleReport: (scope: string, model: WireDetailModel) => Promise<void>;
  /** List the scope's detail models. Best effort: a list that cannot be read is an empty one. */
  load: (scope: string) => Promise<void>;
  /** Load one into the scene with the elements it was made from, put its check back on the Clashes
   *  tab, and its joints into the produced section. */
  open: (scope: string, model: WireDetailModel) => Promise<void>;
  /** Delete one (the caller asks first). */
  remove: (scope: string, detailId: string) => Promise<void>;
}

export const useDetailModelsStore = create<DetailModelsState>((set, get) => ({
  scope: null,
  models: [],
  busyId: null,
  error: null,
  notes: [],
  reportId: null,
  reports: {},
  deps: realDeps,

  toggleReport: async (scope, model) => {
    const id = model.detail_id;
    if (get().reportId === id) {
      set({ reportId: null });
      return;
    }
    set({ reportId: id });
    const have = get().reports[id];
    if (have && have.status !== "error") return;
    set((s) => ({ reports: { ...s.reports, [id]: { status: "loading" } } }));
    let report: DetailReport;
    try {
      report = { status: "ready", stats: parseDetailStats(await get().deps.stats(scope, model.stats_key)) };
    } catch (e) {
      report = { status: "error", error: `Could not read its take-off: ${e instanceof Error ? e.message : String(e)}` };
    }
    set((s) => ({ reports: { ...s.reports, [id]: report } }));
  },

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
    set({ busyId: model.detail_id, error: null, notes: [] });
    const deps = get().deps;
    const notes: string[] = [];
    // The check it came from; one that cannot be read is said, and the model loads on its own.
    let ctx: DetailContext | null = null;
    try {
      ctx = await deps.context(scope, model);
      notes.push(...ctx.notes);
    } catch (e) {
      notes.push(`The clash check it was made from could not be read: ${e instanceof Error ? e.message : String(e)}`);
    }
    try {
      // The model goes in last of the state (`applySceneState`'s order), so it is the loaded source
      // the Clashes tab sees -- and a detail model is never followed as the thing a check is of.
      const out = await deps.apply(scope, detailModelState(model, ctx), {
        ...(ctx ? { membersName: ctx.label, clashResult: ctx.result } : {}),
      });
      notes.push(...out.notes);
      set({
        busyId: null,
        notes,
        ...(out.detailError ? { error: `Could not load ${model.label}: ${out.detailError}` } : {}),
      });
    } catch (e) {
      set({ busyId: null, notes, error: `Could not load ${model.label}: ${e instanceof Error ? e.message : String(e)}` });
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
    const lost = detailModelLosses(m);
    if (lost) parts.push(lost);
  } else {
    parts.push(m.status === "error" ? "failed" : m.status);
  }
  return parts.join(" · ");
}

/** What a done model's report has to say, as "N joints not built · M welds failed · K duplicate
 *  welds dropped" -- `null` when nothing (and then there is no report to open). A record from before
 *  the structured counts says only how many joints were skipped; one from before seams were
 *  de-duplicated has no duplicates. */
export function detailModelLosses(m: WireDetailModel): string | null {
  const c = m.counts;
  if (!c) return null;
  const notBuilt = c.not_built ?? c.skipped ?? 0;
  const welds = c.weld_failures ?? 0;
  const duplicates = c.duplicate_welds ?? 0;
  const out: string[] = [];
  if (notBuilt) out.push(c.not_built == null ? `${notBuilt} skipped` : `${notBuilt} joint${notBuilt === 1 ? "" : "s"} not built`);
  if (welds) out.push(`${welds} weld${welds === 1 ? "" : "s"} failed`);
  if (duplicates) out.push(`${duplicates} duplicate weld${duplicates === 1 ? "" : "s"} dropped`);
  return out.length ? out.join(" · ") : null;
}

/** The name of a model's report, by what is in it: "Not built" (joints or welds missing),
 *  "Duplicate welds" (only welds dropped as duplicates), or both. */
export function detailReportLabel(m: WireDetailModel): string {
  const c = m.counts;
  const missing = !!(c && ((c.not_built ?? c.skipped ?? 0) || (c.weld_failures ?? 0)));
  const duplicates = !!(c && (c.duplicate_welds ?? 0));
  if (missing && duplicates) return "Not built / duplicates";
  return duplicates ? "Duplicate welds" : "Not built";
}
