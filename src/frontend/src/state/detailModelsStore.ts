// The scope's kept detail models (Connections > Review, "Detail models"): every detail run of a
// clash result or a convex-contact run is a model of its own on the server (`ada.comms.rest.
// clash_details`), listed here newest first, loaded back into the scene, or deleted.
//
// Loading one puts back what the check and the detail run left on screen: the elements that were
// checked are loaded (as a saved group's members are), the check's target and result go back on the
// Clashes tab with its markers, and then -- what the end of a fresh detail run does -- the GLB is
// overlaid as a source of its own (`overlay_file_in_scene`, the Clashes tab's post-job load), its
// take-off joins the produced joints the Review tab lists (`clashCheckStore.producedJoints`) and it
// is the active detail model. What cannot be brought back is said, and the model loads regardless.

import { create } from "zustand";

import { clashCheckApi, type WireDetailModel } from "@/services/api/clashCheck";
import { capabilities } from "@/services/capabilities";
import { CONTACT_ORIGIN, contactRunsApi } from "@/services/clash/contactRuns";
import { mergeProducedJoints, parseDetailStats, useClashCheckStore, type DetailStats } from "@/state/clashCheckStore";
import { detailContext, type DetailContext } from "@/state/clashCheck/detailContext";
import type { GroupMember } from "@/utils/groups/savedGroups";

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
  overlay(scope: string, glbKey: string): Promise<void>;
  /** The check the model was detailed from (`state/clashCheck/detailContext`). */
  context(scope: string, model: WireDetailModel): Promise<DetailContext>;
  /** Load the checked elements into the scene, as a saved group's "Load into scene" does (what is
   *  in the scene already stays as it is); returns what could not be loaded, in sentences. */
  loadInputs(scope: string, name: string, members: readonly GroupMember[]): Promise<readonly string[]>;
  /** Start the clash markers following the store (idempotent; the Clashes tab starts them too). */
  markers(): Promise<void>;
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
  async loadInputs(scope, name, members) {
    if (members.length === 0) return [];
    const [{ liveGroupLoadEnv }, { groupLoadNote }] = await Promise.all([
      import("@/plugins/sceneGroupLoad"),
      import("@/utils/groups/groupLoadNote"),
    ]);
    const group = { id: `detail-inputs:${name}`, name, created_at: "", members };
    const out = await liveGroupLoadEnv(() => scope).load(group, scope);
    return out.failed.length ? [groupLoadNote(out).text] : [];
  },
  async markers() {
    const { startClashMarkerSync } = await import("@/utils/scene/clashJointMarkers");
    startClashMarkerSync();
  },
};

/** Put a check back on the Clashes tab as its run left it: its target, then its result. */
function restoreCheck(ctx: DetailContext): void {
  const clash = useClashCheckStore.getState();
  const t = ctx.target;
  if (t.kind === "file") clash.setSource(t.sourceKey, t.sourceKey);
  else clash.setAssetTarget(ctx.label, t);
  useClashCheckStore.setState({
    result: ctx.result,
    derivedKey: ctx.derivedKey,
    runKey: ctx.runKey,
    cached: true,
    error: null,
    selectedGroup: null,
    selectedJoints: [],
    selectedJoint: null,
    ...(ctx.runKey ? { checker: CONTACT_ORIGIN } : {}),
  });
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
    try {
      // The check first: the elements it checked into the scene, then its target and result on the
      // Clashes tab (which clears a different check's produced joints), and its markers. A check that
      // cannot be read is said, and the model is loaded on its own as before.
      try {
        const ctx = await deps.context(scope, model);
        notes.push(...ctx.notes);
        try {
          notes.push(...(await deps.loadInputs(scope, ctx.label, ctx.members)));
        } catch (e) {
          notes.push(`The checked elements could not be loaded: ${e instanceof Error ? e.message : String(e)}`);
        }
        restoreCheck(ctx);
        await deps.markers();
      } catch (e) {
        notes.push(`The clash check it was made from could not be read: ${e instanceof Error ? e.message : String(e)}`);
      }
      // Then the model itself, last, so it is the loaded source the Clashes tab sees (a detail model
      // is never followed as the thing a check is of).
      await deps.overlay(scope, model.glb_key);
      let stats = parseDetailStats(null);
      try {
        stats = parseDetailStats(await deps.stats(scope, model.stats_key));
      } catch {
        // The geometry is loaded; a take-off that cannot be read leaves the produced list as it was.
      }
      useClashCheckStore.setState((s) => ({
        producedJoints: mergeProducedJoints(s.producedJoints, stats.joints),
        producedSkipped: [...s.producedSkipped, ...stats.skipped.filter((x) => !s.producedSkipped.includes(x))],
        // The active detail model, as a detail run leaves it.
        detailDerivedKey: model.glb_key,
        detailGlbKey: model.glb_key,
        detailSpec: model.spec,
        detailError: null,
      }));
      set({ busyId: null, notes });
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
    const lost = detailModelLosses(m);
    if (lost) parts.push(lost);
  } else {
    parts.push(m.status === "error" ? "failed" : m.status);
  }
  return parts.join(" · ");
}

/** What a done model is missing, as "N joints not built · M welds failed" -- `null` when nothing
 *  is. A record from before the structured counts says only how many joints were skipped. */
export function detailModelLosses(m: WireDetailModel): string | null {
  const c = m.counts;
  if (!c) return null;
  const notBuilt = c.not_built ?? c.skipped ?? 0;
  const welds = c.weld_failures ?? 0;
  const out: string[] = [];
  if (notBuilt) out.push(c.not_built == null ? `${notBuilt} skipped` : `${notBuilt} joint${notBuilt === 1 ? "" : "s"} not built`);
  if (welds) out.push(`${welds} weld${welds === 1 ? "" : "s"} failed`);
  return out.length ? out.join(" · ") : null;
}
