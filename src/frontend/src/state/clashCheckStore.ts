// The Connections panel's "Clashes" tab: run a clash check over the loaded model's SOURCE, read back
// `ada.clash/result@1`, and hand a chosen group off to a generator.
//
// ONE RESULT, READ BY EVERYTHING. Every badge, count and filter the panel shows is a pure
// function of the single `result` object this store holds -- never per-row state, never
// recomputed independently by two call sites. That is the same discipline `@/assets/assetView`
// holds for the asset browser (`notes_core_asset_browser.md` §Verification "One derived view"),
// and it is why every derivation below (`groupFacets`, `filteredGroups`, `isSpecAvailable`,
// `noMembersSentence`, `jointsForGroup`) is a plain function of `(result, ...)` rather than a
// second piece of state the store would have to keep in sync with the first.
//
// WHY IDENTIFICATION AND REVIEW SHARE NOTHING HERE BUT THE RESULT SCHEMA. This store is the
// "identify" half of the workflow; the "review" half is the Connections panel's Review tab (it was `Joints`)
// (`JointsOverviewPanel.tsx`), which reads this store's `result.joints` ALONGSIDE the
// take-off's produced joints rather than duplicating a table. `Clashes` -> Review is one
// workflow, not two features that happen to look similar (Decision 10's naming paragraph).

import { create } from "zustand";
import { browserClashCheckSupports, runBrowserClashCheck } from "@/services/clash/browserClashCheck";
import { clashCheckApi, type ClashCheckOptions, type ClashCheckTarget } from "@/services/api/clashCheck";
import { annotateRunFlow, CONTACT_ORIGIN, contactRunsApi, contactTargetFor, glbKeyForSource, runBrowserContactFlow, runBrowserGroupContactFlow, runWorkerContactFlow, type ContactRunDoc } from "@/services/clash/contactRuns";
import { settingsApi } from "@/services/api/settings";
import { CLASH_SPEC_PROVIDERS_KEY, parseSpecProviders, preferenceFor } from "@/services/clashSpecProviders";
import { serverCheckMembers } from "@/utils/groups/groupSourceRefs";
import { describeMember } from "@/utils/groups/savedGroups";
import { DEFAULT_CLASH_FILTERS, sameClashTarget } from "./clashCheck/derive";
import { BUILTIN_CHECKER } from "./clashCheck/result";
import { checkRequestOptions, contactGeometryProvider, effectiveSpecProviders, runGeometryAwareAnnotateFlow, runGeometryAwareCheckFlow, type GeometryAnnotateFlowDeps, type GeometryCheckFlowDeps, type GeometryPromptAnswer } from "./clashCheck/geometry";
import { detailBatches, mergeProducedJoints } from "./clashCheck/detail";
import { runClashCheckFlow } from "./clashCheck/flows";
import type { ClashCheckState } from "./clashCheck/state";
import { contactRunOf, contactRunOptions, contactRunToResult, contactWasmAvailable, detailOnce, groupContactRequest, nodeGlbKey, realContactDeps, realFlowDeps } from "./clashCheck/realDeps";
import { resolveChecker, runsInBrowser, withBrowserContact } from "./clashCheck/engine";

export { contactRunOptions, contactRunToResult, contactRunOf, groupContactElements } from "./clashCheck/realDeps";
export { BUILTIN_CHECKER_DESCRIPTION, BUILTIN_CHECKER_LABEL, CONTACT_CHECKER_LABEL, BROWSER_CONTACT_CHECKER, CONTACT_UNAVAILABLE_NOTE, contactOffered, resolveChecker, runsInBrowser, withBrowserContact } from "./clashCheck/engine";

export { type ClashApplicableSpec, type ClashJointMember, type ClashContact, type ClashJoint, type ClashPassReport, type ClashGroup, type ClashResult, ClashResultError, BUILTIN_CHECKER, parseClashResult } from "./clashCheck/result";
export { withoutHiddenOrigins, visibleResult, originsInResult, jointsForGroup, memberNamesForGroup, type ClashGroupFacets, groupFacets, facetOptions, type ClashFilters, DEFAULT_CLASH_FILTERS, filteredGroups, noMembersSentence, isolationMembers, checkedSourceName, sameClashTarget, isSpecAvailable } from "./clashCheck/derive";
export { groupColor, hueColor, type JointMarker, jointMarkers } from "./clashCheck/markers";
export { specForJoint, detailBatches, type ProducedJointType, type ProducedJointItem, type ProducedJoints, type DetailStats, parseDetailStats, mergeProducedJoints } from "./clashCheck/detail";
export { type ClashFlowApi, type ClashFlowDeps, runClashCheckFlow, runClashDetailFlow } from "./clashCheck/flows";
export { type GeometryRequestPrompt, type GeometryPromptAnswer, geometryRequestPrompt, geometryPromptSentence, type GeometryStepDeps, type GeometryCheckFlowDeps, type GeometryCheckOutcome, runGeometryAwareCheckFlow, contactGeometryProvider, type GeometryAnnotateFlowDeps, type GeometryAnnotateOutcome, runGeometryAwareAnnotateFlow, checkRequestOptions, effectiveSpecProviders, type GeometryProviderChoice, UNREADABLE_GEOMETRY_REASON, targetProviders, geometryProviderChoices, geometrySourceOf } from "./clashCheck/geometry";

/** The answer the open `geometryPrompt` is waiting for. One at a time: `runCheck` refuses to start
 *  while busy, and the prompt is part of a run. */
let pendingGeometryAnswer: ((answer: GeometryPromptAnswer) => void) | null = null;

/** The live deps of the missing-geometry step. Imported on first use: they reach the asset browser,
 *  the plugin listing and the request form's job deps, none of which a check without a geometry
 *  provider -- or a test of this store -- should have to load. */
async function realGeometryDeps(
  scope: string,
  set: (patch: Partial<ClashCheckState>) => void,
): Promise<GeometryCheckFlowDeps> {
  const live = await import("@/state/clashGeometryDeps");
  return {
    ...realFlowDeps(scope),
    ...(await live.geometryRequestDeps(scope, (stage) => set({ geometryStage: stage }))),
    prompt: (question) =>
      new Promise<GeometryPromptAnswer>((resolve) => {
        pendingGeometryAnswer = resolve;
        set({ geometryPrompt: question });
      }),
    onStage: (stage) => set({ geometryStage: stage }),
  };
}

/** The live deps of a convex-contact run's annotate through a geometry provider: the same
 *  missing-geometry step, and the run's own REST deps. */
async function realAnnotateDeps(
  scope: string,
  set: (patch: Partial<ClashCheckState>) => void,
): Promise<GeometryAnnotateFlowDeps> {
  const { api: _api, trackJob: _trackJob, wait: _wait, now: _now, ...step } = await realGeometryDeps(scope, set);
  return { ...step, contact: realContactDeps(scope) };
}

/** Annotate a stored run through `provider` (the missing-geometry step first) and put it on the
 *  panel. Shared by a fresh run and "Resolve specs" on a reopened one. */
async function annotateThroughProvider(
  scope: string,
  target: ClashCheckTarget,
  run: ContactRunDoc,
  runKey: string,
  provider: string,
  set: (patch: Partial<ClashCheckState>) => void,
): Promise<void> {
  set({ geometryNotes: [], geometryStage: null, browserStage: null });
  const outcome = await runGeometryAwareAnnotateFlow(await realAnnotateDeps(scope, set), scope, target, runKey, provider);
  set({
    result: contactRunToResult(outcome?.run ?? run, runKey),
    runKey,
    derivedKey: null,
    cached: false,
    busy: false,
    browserStage: null,
    geometryStage: null,
    geometryNotes: outcome ? outcome.notes : ["Cancelled; the run is stored without its specs."],
    selectedGroup: null,
    selectedJoints: [],
    selectedJoint: null,
  });
}

const INITIAL_OPTIONS: ClashCheckOptions = {
  out_of_plane_tol: 0.1,
  point_tol: 1e-5,
  root: null,
  include_plate_joints: true,
};

export const useClashCheckStore = create<ClashCheckState>((set, get) => ({
  sourceName: null,
  assetTarget: null,
  sourceKey: null,
  options: INITIAL_OPTIONS,
  filters: DEFAULT_CLASH_FILTERS,
  selectedGroup: null,
  selectedJoints: [],
  selectedJoint: null,
  isolate: "ghost",
  isolateOpacity: 0.15,
  showMarkers: true,
  showContacts: false,
  markerScale: 1,
  inBrowser: null,
  contactWasm: null,
  browserStage: null,
  selectedPasses: null,
  hiddenOrigins: [],
  availablePasses: null,
  liveCapabilities: null,
  availableCheckers: null,
  // Convex contact, the clash check proper; `resolveChecker` falls back to core's where it cannot run.
  checker: CONTACT_ORIGIN,
  checkerChosen: false,
  checkerNote: null,
  checkerOptions: {},
  specProviders: [],
  adminSpecProviders: null,
  specProvidersOverride: undefined,
  geometryProvider: null,
  availableGeometryProviders: null,
  geometryPrompt: null,
  geometryStage: null,
  geometryNotes: [],

  jobId: null,
  derivedKey: null,
  runKey: null,
  contactRuns: [],
  recentContactRuns: [],
  cached: false,
  result: null,
  busy: false,
  error: null,

  detailBusy: false,
  detailError: null,
  detailSpec: null,
  detailDerivedKey: null,
  detailGlbKey: null,
  producedJoints: null,
  producedSkipped: [],
  detailProgress: null,

  setAssetTarget: (label, target) => {
    // Same invalidation rule as `setSource`, for the same reason: a result rendered against a node
    // that is no longer selected would misreport joints that are not there. Compared by VALUE --
    // the panel builds a fresh object per render, so identity would reset the result on every
    // repaint and lose the run the user just made.
    if (sameClashTarget(get().assetTarget, target)) {
      // Same model, maybe a new name for it (a renamed group): keep the result, update the label.
      if (label !== get().sourceName) set({ sourceName: label });
      // The same members picked from the saved groups now name the group too: kept, so a run is
      // filed under it.
      const was = get().assetTarget;
      if (target?.kind === "group" && was?.kind === "group" && (target.id ?? null) !== (was.id ?? null)) {
        set({ assetTarget: target });
      }
      return;
    }
    // A question about the previous target's members no longer means anything.
    if (get().geometryPrompt) get().answerGeometryPrompt("cancel");
    set({
      sourceName: label,
      sourceKey: null,
      assetTarget: target,
      result: null,
      derivedKey: null,
      runKey: null,
      contactRuns: [],
      cached: false,
      error: null,
      selectedGroup: null,
      selectedJoints: [],
      selectedJoint: null,
      detailDerivedKey: null,
      detailGlbKey: null,
      producedJoints: null,
      producedSkipped: [],
    });
  },

  setGroupTarget: (group) => {
    get().setAssetTarget(`group ${group.name}`, {
      kind: "group",
      name: group.name,
      members: [...group.members],
      ...(group.id ? { id: group.id } : {}),
    });
  },

  followLoadedSource: (sourceName) => {
    if (get().assetTarget !== null) return;
    get().setSource(sourceName, sourceName);
  },

  setSource: (sourceName, sourceKey) => {
    // A different source invalidates the previous run's result -- a stale clash result rendered
    // against a model that is no longer loaded would misreport joints that aren't there.
    if (get().sourceKey !== sourceKey) {
      set({
        sourceName,
        sourceKey,
        // Only one thing is being checked at a time.
        assetTarget: null,
        result: null,
        derivedKey: null,
        runKey: null,
        contactRuns: [],
        cached: false,
        error: null,
        selectedGroup: null,
        selectedJoints: [],
        selectedJoint: null,
        detailDerivedKey: null,
        detailGlbKey: null,
        producedJoints: null,
        producedSkipped: [],
        detailError: null,
      });
    } else {
      set({ sourceName });
    }
  },
  setOptions: (patch) => set((s) => ({ options: { ...s.options, ...patch } })),
  setFilters: (patch) => set((s) => ({ filters: { ...s.filters, ...patch } })),
  selectGroup: (typeKey) => set({ selectedGroup: typeKey, selectedJoints: [], selectedJoint: null }),
  selectJoints: (ids) => set({ selectedJoints: ids }),
  focusJoint: (id) => {
    if (id === null) {
      set({ selectedJoint: null });
      return;
    }
    // Opening the joint's group with it is what makes a marker click land somewhere visible: the
    // row exists only inside its group, and the groups are collapsed by default.
    const typeKey = get().result?.jointsById.get(id)?.typeKey ?? null;
    set({ selectedJoint: id, selectedGroup: typeKey ?? get().selectedGroup });
  },
  setIsolate: (mode) => set({ isolate: mode }),
  setIsolateOpacity: (v) => set({ isolateOpacity: Math.max(0.02, Math.min(1, v)) }),
  setShowMarkers: (v) => set({ showMarkers: v }),
  setShowContacts: (v) => set({ showContacts: v }),
  setMarkerScale: (v) => set({ markerScale: Math.max(0.1, Math.min(10, v)) }),
  setInBrowser: (v) => set({ inBrowser: v }),
  setSelectedPasses: (names) => set({ selectedPasses: names }),
  setChecker: (name) => set({ checker: name, checkerChosen: true, checkerNote: null, selectedPasses: null }),
  setCheckerOption: (key, value) =>
    set((s) => ({
      checkerOptions: { ...s.checkerOptions, [s.checker]: { ...(s.checkerOptions[s.checker] ?? {}), [key]: value } },
    })),
  setSpecProvidersOverride: (pref) => set({ specProvidersOverride: pref }),
  setGeometryProvider: (id) => set({ geometryProvider: id || null }),
  answerGeometryPrompt: (answer) => {
    const resolve = pendingGeometryAnswer;
    pendingGeometryAnswer = null;
    set({ geometryPrompt: null });
    resolve?.(answer);
  },

  loadCapabilities: async (scope) => {
    // All optional knowledge: failing to learn any of it must leave the panel usable, not
    // empty. Settled independently so one missing route does not cost the others' answers.
    const [passes, caps, checkers, specProviders, geometry, wasm] = await Promise.allSettled([
      clashCheckApi.listPasses(scope as never),
      clashCheckApi.listLiveConnectionCapabilities(scope as never),
      clashCheckApi.listCheckers(scope as never),
      settingsApi.getPublicSetting(CLASH_SPEC_PROVIDERS_KEY),
      clashCheckApi.listGeometryProviders(scope as never),
      // Whether convex contact -- the default engine -- can run in this browser at all.
      get().contactWasm === null ? contactWasmAvailable() : Promise.resolve(get().contactWasm as boolean),
    ]);
    const liveCapabilities = caps.status === "fulfilled" ? caps.value : null;
    const contactWasm = wasm.status === "fulfilled" ? wasm.value : false;
    // The deployment's engines, plus convex contact where this viewer runs it and the deployment
    // (an api without adacpp) does not list it.
    const availableCheckers = withBrowserContact(checkers.status === "fulfilled" ? checkers.value : null, contactWasm);
    const availableGeometryProviders = geometry.status === "fulfilled" ? geometry.value : null;
    set((s) => {
      // A checker chosen earlier whose pool has since gone is dropped back to core's, rather than
      // left selected for a run the route will refuse; one never chosen follows the default.
      const engine = resolveChecker({ ...s, availableCheckers, contactWasm });
      return {
        contactWasm,
        checker: engine.checker,
        checkerNote: engine.note,
        availableGeometryProviders,
        // A provider chosen earlier that no live pool can read any more falls back to each member's
        // own, rather than staying selected for a run that would read nothing.
        geometryProvider:
          availableGeometryProviders &&
          s.geometryProvider &&
          !availableGeometryProviders.some((p) => p.id === s.geometryProvider && p.readable)
            ? null
            : s.geometryProvider,
        availablePasses: passes.status === "fulfilled" ? passes.value : null,
        liveCapabilities,
        availableCheckers,
        specProviders: [...(liveCapabilities ?? [])].sort(),
        adminSpecProviders:
          specProviders.status === "fulfilled" ? preferenceFor(parseSpecProviders(specProviders.value), scope) : null,
      };
    });
  },
  toggleOriginHidden: (origin) =>
    set((s) => ({
      hiddenOrigins: s.hiddenOrigins.includes(origin)
        ? s.hiddenOrigins.filter((o) => o !== origin)
        : [...s.hiddenOrigins, origin],
    })),

  runCheck: async (scope) => {
    const { sourceKey, assetTarget, options } = get();
    // A node's model is read by its provider on a worker, and a group combines several models on
    // the server, so the in-browser path below can serve neither: it scans ONE FILE this process
    // fetched. Falling through to the server is not a degradation here, it is the only way the
    // check can be made at all.
    if (!sourceKey && !assetTarget) return;
    if (get().busy) return;
    set({ busy: true, error: null, browserStage: null });
    try {
      // A run before the panel's probe answered: ask now whether the default engine can run here.
      if (get().contactWasm === null) {
        const contactWasm = await contactWasmAvailable();
        const availableCheckers = withBrowserContact(get().availableCheckers, contactWasm);
        const engine = resolveChecker({ ...get(), availableCheckers, contactWasm });
        set({ contactWasm, availableCheckers, checker: engine.checker, checkerNote: engine.note });
      }
      // The engine for THIS run. Convex contact by default; where it was not picked explicitly and
      // cannot measure this target, the run uses core's check instead, and says so, rather than
      // failing: a group not picked from the saved groups has nothing to file a run under, and a
      // node whose geometry is a provider's mesh delivery has no stored GLB to measure.
      let checker = get().checker;
      let checkerNote = resolveChecker(get()).note;
      if (checker === CONTACT_ORIGIN && !get().checkerChosen) {
        const t = contactTargetFor(sourceKey, assetTarget as never);
        const why = !t
          ? "this group was not picked from the saved groups, so a run has nothing to be filed under (save it as a group to measure it)"
          : t.kind === "node" && !(await nodeGlbKey(t))
            ? "this node's geometry is not a stored GLB (a provider's mesh delivery)"
            : null;
        if (why) {
          checker = BUILTIN_CHECKER;
          checkerNote = `Convex contact cannot measure this target -- ${why} -- so this run used the built-in member-connectivity check.`;
        }
      }
      set({ checkerNote });
      const inBrowser = runsInBrowser({ ...get(), checker });
      // The convex-contact CLASH check measures the viewer GLB -- in this browser (adacpp's wasm
      // module) or on a worker (the same C++ on the same stored bytes) -- and the run is kept beside
      // the model, so it outlives this page and a detail is made from it (`services/clash/contactRuns`).
      if (checker === CONTACT_ORIGIN) {
        const target = contactTargetFor(sourceKey, assetTarget as never);
        if (!target) {
          throw new Error(
            assetTarget?.kind === "group"
              ? "This group was not picked from the saved groups, so a clash run has nothing to be filed under: save it as a group and check it from there."
              : "Nothing to check: pick a model, a published node or a saved group.",
          );
        }
        const options = contactRunOptions(get().checkerOptions[CONTACT_ORIGIN]);
        // Members whose own provider cannot be read into objects (a mesh-only publish) are read for
        // the specs through one that can: the run is then stored unannotated, and annotated through
        // that provider once the missing-geometry step has had its answer.
        const provider = contactGeometryProvider(assetTarget, get().geometryProvider, get().availableGeometryProviders);
        set({ geometryNotes: [] });
        if (target.kind === "group") {
          if (assetTarget?.kind !== "group") throw new Error("A group run needs the group itself as the target.");
          // A group spans several GLBs, which only the browser reads together (`glbMembers` +
          // `findConvexContacts`); a worker has no such verb, so this runs here whatever the
          // in-browser switch says.
          const onStage = (stage: string) => set({ browserStage: stage });
          const req = await groupContactRequest(scope, { ...assetTarget, id: target.group_id }, options, onStage);
          const { run, runKey } = await runBrowserGroupContactFlow(
            realContactDeps(scope),
            scope,
            provider ? { ...req, annotate: false } : req,
            onStage,
          );
          if (provider) {
            await annotateThroughProvider(scope, assetTarget, run, runKey, provider, set);
            void get().loadContactRuns(scope);
            return;
          }
          set({
            result: contactRunToResult(run, runKey),
            runKey,
            derivedKey: null,
            cached: false,
            busy: false,
            browserStage: null,
            selectedGroup: null,
            selectedJoints: [],
            selectedJoint: null,
          });
          void get().loadContactRuns(scope);
          return;
        }
        const glbKey = target.kind === "file" ? glbKeyForSource(target.source_key) : await nodeGlbKey(target);
        if (!glbKey) {
          throw new Error("This node's geometry is not a stored GLB (a provider's mesh delivery), so it cannot be checked.");
        }
        const req = { target, glbKey, upAxis: "z" as const, options, ...(provider ? { annotate: false } : {}) };
        const { run, runKey } = inBrowser
          ? await runBrowserContactFlow(realContactDeps(scope), scope, req, (stage) => set({ browserStage: stage }))
          : await runWorkerContactFlow(realContactDeps(scope), scope, req);
        if (provider && assetTarget) {
          await annotateThroughProvider(scope, assetTarget, run, runKey, provider, set);
          void get().loadContactRuns(scope);
          return;
        }
        set({
          result: contactRunToResult(run, runKey),
          runKey,
          derivedKey: null,
          cached: false,
          busy: false,
          browserStage: null,
          selectedGroup: null,
          selectedJoints: [],
          selectedJoint: null,
        });
        void get().loadContactRuns(scope);
        return;
      }
      // The browser runs core's checker only; a contributed engine lives on its pool.
      if (
        sourceKey &&
        !assetTarget &&
        inBrowser &&
        checker === BUILTIN_CHECKER &&
        browserClashCheckSupports(sourceKey)
      ) {
        // No job, no upload: the file is fetched once for the scan and everything else happens
        // here. `derivedKey` stays null on purpose -- there IS no server-side document to point a
        // detail hand-off at, and a key that resolved to nothing would fail later and further away
        // than an honest null does. The joint IDS are identical either way, so a user who wants to
        // DETAIL a joint re-runs on the server and picks the same joint.
        const bytes = await clashCheckApi.getSourceBytes(scope, sourceKey);
        const { result, beams } = await runBrowserClashCheck(sourceKey, bytes, options, (stage, detail) =>
          set({ browserStage: detail ? `${stage} (${detail})` : stage }),
        );
        set({
          result,
          derivedKey: null,
          cached: false,
          busy: false,
          browserStage: null,
          selectedGroup: null,
          selectedJoints: [],
          selectedJoint: null,
          error: beams === 0 ? "This IFC states no beams, so there is nothing to check." : null,
        });
        return;
      }
      // A group is read on the server from its members' SOURCES (`ada.clash.group_model`): a member
      // opened from a local file has none there and is left out, said so; one with no revision is
      // pinned to the source it was saved against (`serverCheckMembers`).
      let sourceNotes: string[] = [];
      let groupTarget = assetTarget;
      if (assetTarget?.kind === "group") {
        const { members, skipped } = serverCheckMembers(assetTarget.members);
        if (members.length === 0) {
          throw new Error(
            "Every member of this group is in a model opened from a local file: nothing of it is stored, so the server has no source to check.",
          );
        }
        if (skipped.length) {
          sourceNotes = [
            `${skipped.length} member${skipped.length === 1 ? " is" : "s are"} in a model opened from a local file, with no source in the store, and ${skipped.length === 1 ? "was" : "were"} left out: ${skipped.slice(0, 3).map(describeMember).join("; ")}${skipped.length > 3 ? "; …" : ""}`,
          ];
        }
        groupTarget = { ...assetTarget, members };
      }
      const target = groupTarget ?? { kind: "file" as const, sourceKey: sourceKey as string };
      // A plain file has no provider to read it from, so the choice is not sent for one.
      // A pass selection belongs to the engine it was made for: a run that fell back to core's takes
      // core's default set rather than convex contact's passes.
      const requestOptions = checkRequestOptions({
        ...get(),
        checker,
        ...(checker !== get().checker ? { selectedPasses: null } : {}),
        geometryProvider: assetTarget ? get().geometryProvider : null,
      });
      if (assetTarget && requestOptions.geometry_provider) {
        // Through a geometry provider: find the members it lacks and offer to request them first.
        set({ geometryNotes: sourceNotes, geometryStage: null });
        const outcome = await runGeometryAwareCheckFlow(await realGeometryDeps(scope, set), scope, target, requestOptions);
        if (outcome === null) {
          set({ busy: false, geometryStage: null, geometryNotes: ["Cancelled; nothing was checked."] });
          return;
        }
        const { result, derivedKey, cached, notes } = outcome;
        set({
          result,
          derivedKey,
          cached,
          busy: false,
          geometryStage: null,
          geometryNotes: [...sourceNotes, ...notes],
          selectedGroup: null,
          selectedJoints: [],
          selectedJoint: null,
        });
        return;
      }
      if (sourceNotes.length) set({ geometryNotes: sourceNotes });
      const { result, derivedKey, cached } = await runClashCheckFlow(realFlowDeps(scope), scope, target, requestOptions);
      set({ result, derivedKey, cached, busy: false, selectedGroup: null, selectedJoints: [], selectedJoint: null });
    } catch (e) {
      set({
        busy: false,
        browserStage: null,
        geometryStage: null,
        geometryPrompt: null,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },

  loadContactRuns: async (scope) => {
    const target = contactTargetFor(get().sourceKey, get().assetTarget as never);
    if (!target) {
      set({ contactRuns: [] });
      return;
    }
    try {
      const runs = await contactRunsApi.listRuns(scope, target);
      // Still the same target? A slow listing must not land on the next model's panel.
      const now = contactTargetFor(get().sourceKey, get().assetTarget as never);
      if (JSON.stringify(now) === JSON.stringify(target)) set({ contactRuns: runs });
    } catch {
      set({ contactRuns: [] });
    }
  },

  openContactRun: async (scope, key) => {
    if (get().busy) return;
    set({ busy: true, error: null });
    try {
      const run = await contactRunsApi.getRun(scope, key);
      set({
        result: contactRunToResult(run, key),
        runKey: key,
        derivedKey: null,
        cached: true,
        busy: false,
        checker: CONTACT_ORIGIN,
        selectedGroup: null,
        selectedJoints: [],
        selectedJoint: null,
      });
    } catch (e) {
      set({ busy: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  loadRecentContactRuns: async (scope) => {
    try {
      set({ recentContactRuns: await contactRunsApi.listRecent(scope) });
    } catch {
      set({ recentContactRuns: [] });
    }
  },

  openRecentContactRun: async (scope, key) => {
    if (get().busy) return;
    set({ busy: true, error: null });
    try {
      const run = await contactRunsApi.getRun(scope, key);
      const t = run.target;
      // The run's own target, so a re-annotate or a detail reads the members it was made on.
      set({ busy: false });
      if (t.kind === "group") {
        get().setGroupTarget({
          id: t.group_id,
          name: run.group?.name ?? t.group_id,
          members: run.group?.members ?? [],
        });
      } else if (t.kind === "node") {
        get().setAssetTarget(t.node ?? t.subject, t);
      } else {
        get().setSource(t.source_key, t.source_key);
      }
      set({
        result: contactRunToResult(run, key),
        runKey: key,
        derivedKey: null,
        cached: true,
        checker: CONTACT_ORIGIN,
        selectedGroup: null,
        selectedJoints: [],
        selectedJoint: null,
      });
      void get().loadContactRuns(scope);
    } catch (e) {
      set({ busy: false, error: e instanceof Error ? e.message : String(e) });
    }
  },

  annotateContactRun: async (scope) => {
    const { runKey, assetTarget, result } = get();
    if (!runKey || get().busy) return;
    const run = contactRunOf(result);
    set({ busy: true, error: null, geometryNotes: [] });
    try {
      const provider = contactGeometryProvider(assetTarget, get().geometryProvider, get().availableGeometryProviders);
      if (provider && assetTarget && run) {
        await annotateThroughProvider(scope, assetTarget, run, runKey, provider, set);
        return;
      }
      const out = await annotateRunFlow(realContactDeps(scope), scope, runKey, null, (stage) => set({ browserStage: stage }));
      set({
        result: contactRunToResult(out.run, runKey),
        busy: false,
        browserStage: null,
        geometryNotes: out.error ? [`The specs were not resolved: ${out.error}`] : [],
      });
    } catch (e) {
      set({
        busy: false,
        browserStage: null,
        geometryStage: null,
        geometryPrompt: null,
        error: e instanceof Error ? e.message : String(e),
      });
    }
  },

  runDetail: async (scope, jointIds, spec) => {
    const { derivedKey, runKey } = get();
    if ((!derivedKey && !runKey) || get().detailBusy) return;
    set({ detailBusy: true, detailError: null, detailSpec: spec, detailDerivedKey: null, detailGlbKey: null });
    try {
      const { derivedKey: detailKey, glbKey, stats } = await detailOnce(get(), scope, jointIds, spec);
      set((s) => ({
        detailBusy: false,
        detailDerivedKey: detailKey,
        detailGlbKey: glbKey ?? detailKey,
        producedJoints: mergeProducedJoints(s.producedJoints, stats.joints),
        producedSkipped: [...s.producedSkipped, ...stats.skipped],
      }));
    } catch (e) {
      set({ detailBusy: false, detailError: e instanceof Error ? e.message : String(e) });
    }
  },

  runDetailAll: async (scope, only) => {
    const { derivedKey, runKey, result } = get();
    if ((!derivedKey && !runKey) || !result || get().detailBusy) return;
    const batches = detailBatches(result, only ?? null, effectiveSpecProviders(get()));
    if (batches.length === 0) {
      set({ detailError: "No joint in this result has a generator that could detail it." });
      return;
    }
    set({
      detailBusy: true,
      detailError: null,
      detailDerivedKey: null,
      detailGlbKey: null,
      detailProgress: { done: 0, total: batches.length },
    });
    // SEQUENTIAL, not parallel: each batch is a worker job, and firing every spec at once would
    // queue work the pools have to serialise anyway while making a partial failure harder to
    // read. Each finished batch sets `detailDerivedKey`, which is what loads it as an overlay --
    // so the joints appear as they are built rather than all at the end.
    let done = 0;
    for (const batch of batches) {
      set({ detailSpec: batch.spec.spec });
      try {
        const { derivedKey: detailKey, glbKey, stats } = await detailOnce(get(), scope, batch.jointIds, batch.spec.spec);
        done += 1;
        set((s) => ({
          detailDerivedKey: detailKey,
          detailGlbKey: glbKey ?? detailKey,
          producedJoints: mergeProducedJoints(s.producedJoints, stats.joints),
          producedSkipped: [...s.producedSkipped, ...stats.skipped],
          detailProgress: { done, total: batches.length },
        }));
      } catch (e) {
        // One spec failing does not cancel the rest: the others are separate jobs against
        // separate joints, and stopping would throw away work already queued.
        set({ detailError: e instanceof Error ? e.message : String(e) });
        done += 1;
        set({ detailProgress: { done, total: batches.length } });
      }
    }
    set({ detailBusy: false, detailProgress: null });
  },

  reset: () => {
    // A run waiting on the prompt is cancelled, not left hanging on an answer nobody can give.
    get().answerGeometryPrompt("cancel");
    set({
      sourceName: null,
      sourceKey: null,
      assetTarget: null,
      options: INITIAL_OPTIONS,
      geometryProvider: null,
      geometryPrompt: null,
      geometryStage: null,
      geometryNotes: [],
      filters: DEFAULT_CLASH_FILTERS,
      selectedGroup: null,
      selectedJoints: [],
      selectedJoint: null,
      jobId: null,
      derivedKey: null,
      runKey: null,
      contactRuns: [],
      cached: false,
      result: null,
      busy: false,
      error: null,
      detailBusy: false,
      detailError: null,
      detailSpec: null,
      detailDerivedKey: null,
      detailGlbKey: null,
      producedJoints: null,
      producedSkipped: [],
      detailProgress: null,
    });
  },
}));
