// The real I/O behind the clash-check flows: the REST client, the job tracker and the scene, and
// the convex-contact runs a group or node check makes.

import { clashCheckApi, type ClashCheckTarget } from "@/services/api/clashCheck";
import { contactRunsApi, glbKeyForSource, runContactDetailFlow, runToResultDoc, type ContactFlowDeps, type ContactRunDoc, type ContactTarget, type GroupContactRunRequest } from "@/services/clash/contactRuns";
import { conversionApi } from "@/services/api/conversion";
import { trackJob } from "@/services/jobTracking";
import { describeMember } from "@/utils/groups/savedGroups";
import { parseClashResult, type ClashResult } from "./result";
import { parseDetailStats, type DetailStats } from "./detail";
import { runClashDetailFlow, type ClashFlowDeps } from "./flows";

export function realFlowDeps(scope: string, onJobStage?: (stage: string | null) => void): ClashFlowDeps {
  return {
    api: {
      runClashCheck: (s, body) => clashCheckApi.runClashCheck(s, body),
      runClashDetail: (s, body) => clashCheckApi.runClashDetail(s, body),
      getClashResult: (s, key) => clashCheckApi.getClashResult(s, key),
      getDetailStats: (s, key) => clashCheckApi.getDetailStats(s, key),
      async jobStatus(jobId) {
        const status = await conversionApi.convertStatus(jobId);
        return { status: status.status, error: status.error, stage: status.stage ?? null, progress: status.progress ?? null };
      },
    },
    trackJob: (opts) => {
      trackJob({ ...opts, scopeUrl: scope });
    },
    onJobStage,
  };
}

/** The live deps of a convex-contact run. The wasm worker is imported on first use: a check on a
 *  worker, or a test of this store, never loads it. */
export function realContactDeps(scope: string, onJobStage?: (stage: string | null) => void): ContactFlowDeps {
  return {
    api: contactRunsApi,
    async measure(glb, opts) {
      const { findGlbContacts } = await import("@/utils/clashContacts/clashContacts");
      return findGlbContacts(glb, opts);
    },
    async glbMembers(glb, upAxis) {
      const { glbMembers } = await import("@/utils/clashContacts/clashContacts");
      return glbMembers(glb, upAxis);
    },
    async convexContacts(soup, opts) {
      const { findConvexContacts } = await import("@/utils/clashContacts/clashContacts");
      return findConvexContacts(soup, opts);
    },
    async jobStatus(jobId) {
      const status = await conversionApi.convertStatus(jobId);
      return { status: status.status, error: status.error, stage: status.stage ?? null, progress: status.progress ?? null };
    },
    trackJob: (opts) => trackJob({ ...opts, scopeUrl: scope }),
    onJobStage,
  };
}

/** Whether this viewer's convex-contact wasm module loads. Never throws: a viewer built without the
 *  module, or a runtime that cannot start the worker, answers false -- the default engine then falls
 *  back to core's, said so (`clashCheck/engine.ts`). */
export async function contactWasmAvailable(): Promise<boolean> {
  try {
    const { clashContactsAvailable } = await import("@/utils/clashContacts/clashContacts");
    return await clashContactsAvailable();
  } catch {
    return false;
  }
}

/** One detail hand-off, from whichever document the result came from: a stored convex-contact RUN
 *  (`runKey`: the stored contacts are the input) or a `result@` document (`derivedKey`: the worker
 *  re-identifies the joints). */
export async function detailOnce(
  state: { derivedKey: string | null; runKey: string | null },
  scope: string,
  ids: readonly string[],
  spec: string,
  onJobStage?: (stage: string | null) => void,
): Promise<{ derivedKey: string; glbKey: string | null; stats: DetailStats }> {
  if (state.runKey && !state.derivedKey) {
    const r = await runContactDetailFlow(realContactDeps(scope, onJobStage), scope, state.runKey, ids, spec);
    return { derivedKey: r.derivedKey, glbKey: r.glbKey, stats: parseDetailStats(r.stats) };
  }
  return runClashDetailFlow(realFlowDeps(scope, onJobStage), scope, state.derivedKey as string, ids, spec);
}

/** The convex-contact checker's settings from the form, with its defaults. */
export function contactRunOptions(checkerOptions: Readonly<Record<string, unknown>> | undefined): ContactRunDoc["options"] {
  const num = (k: string, d: number) => {
    const v = checkerOptions?.[k];
    const n = typeof v === "number" ? v : typeof v === "string" && v.trim() !== "" ? Number(v) : NaN;
    return Number.isFinite(n) && n >= 0 ? n : d;
  };
  return {
    security_margin: num("security_margin", 0.01),
    min_contact_area: num("min_contact_area", 1e-6),
    line_contact_thickness: num("line_contact_thickness", 1e-3),
  };
}

/** The run each result made from one came from: a group run's members are told apart by model and
 *  element id, which the result document (names only) does not carry. */
const runOfResult = new WeakMap<ClashResult, ContactRunDoc>();

/** A stored run as the panel's result. */
export function contactRunToResult(run: ContactRunDoc, runKey: string | null): ClashResult {
  const result = parseClashResult(runToResultDoc(run, runKey));
  runOfResult.set(result, run);
  return result;
}

/** The convex-contact run `result` was made from, if it was. */
export function contactRunOf(result: ClashResult | null): ContactRunDoc | null {
  return (result && runOfResult.get(result)) ?? null;
}

/** A group run's clash members as (model, element id) -- what the scene selects them by, since
 *  names repeat across models. Empty for a run over one model, or an id not in the run. */
export function groupContactElements(
  run: ContactRunDoc,
  contactId: string,
): { source: NonNullable<ContactRunDoc["geometry"]["parts"]>[number]["source"]; element: string }[] {
  const parts = run.geometry.parts ?? [];
  const c = run.contacts.find((x) => x.id === contactId);
  if (!c || run.target.kind !== "group") return [];
  return c.members.flatMap((k) => {
    const m = run.members[k];
    const part = m?.part !== undefined ? parts[m.part] : undefined;
    return part && m.element !== undefined ? [{ source: part.source, element: m.element }] : [];
  });
}

/** What a group's convex-contact run measures, from the scene as it is now: the models the members
 *  are in (each loaded first if it is not -- the same "Load into scene" the group offers) and their
 *  element ids. Imported on first use: it reaches the scene and the delivery code. */
export async function groupContactRequest(
  scope: string,
  target: Extract<ClashCheckTarget, { kind: "group" }> & { id: string },
  options: ContactRunDoc["options"],
  onStage: (stage: string) => void,
): Promise<GroupContactRunRequest> {
  const { groupSceneParts } = await import("@/utils/groups/groupScene");
  const group = { id: target.id, name: target.name, created_at: "", members: target.members };
  let found = groupSceneParts(group.members);
  if (found.notLoaded.length) {
    onStage("loading the group's models");
    const [{ loadSavedGroup }, { realDeliveryDeps }, { getSingletonViewerStores }] = await Promise.all([
      import("@/utils/groups/groupLoad"),
      import("@/components/asset_browser/AssetsTab"),
      import("@/state/AdaViewerContext"),
    ]);
    const deps = realDeliveryDeps((name) => getSingletonViewerStores().useModelState.getState().loadedSourceNames.has(name));
    await loadSavedGroup(group, scope, deps, onStage);
    found = groupSceneParts(group.members);
  }
  if (found.notLoaded.length) {
    throw new Error(
      `${found.notLoaded.length} member(s) of group "${target.name}" are in models that could not be loaded, so the ` +
        `group cannot be checked whole: ${found.notLoaded.map(describeMember).join("; ")}`,
    );
  }
  const { useAssetBrowserStore } = await import("@/state/assetBrowserStore");
  const loadedAssets = useAssetBrowserStore.getState().loaded;
  const parts = found.parts.map((p) => {
    const sourceName = p.root.source_name ?? "";
    const glbKey =
      p.target.kind === "file" ? glbKeyForSource(p.target.source_key) : loadedAssets.find((a) => a.sourceName === sourceName)?.glbKey;
    if (!glbKey) {
      throw new Error(
        `The geometry of ${describeMember({ target: p.target, element: null, path: [] })} is not a stored GLB (a provider's mesh delivery), so the group cannot be checked.`,
      );
    }
    return { glbKey, source: p.target, elements: p.elements };
  });
  return {
    target: { kind: "group", group_id: target.id },
    group: { id: target.id, name: target.name, members: [...target.members] },
    parts,
    upAxis: "z",
    options,
    unresolved: found.notFound.map(describeMember),
  };
}

/** The GLB a node target's viewer loaded -- known for a built node (its `glbKey`); a provider's mesh
 *  delivery is an external URL, not a stored GLB, and is measured in the browser only. */
export async function nodeGlbKey(target: Extract<ContactTarget, { kind: "node" }>): Promise<string | null> {
  const { useAssetBrowserStore } = await import("@/state/assetBrowserStore");
  const hit = useAssetBrowserStore
    .getState()
    .loaded.find(
      (l) =>
        l.ref.collection === target.collection &&
        l.ref.subject === target.subject &&
        (l.ref.node ?? null) === (target.node ?? null) &&
        (!target.revision || l.revision === target.revision),
    );
  return hit?.glbKey ?? null;
}
