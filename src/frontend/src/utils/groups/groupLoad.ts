// "Load into scene" for a saved group: put the group's MEMBERS in the scene from what is ALREADY
// published or stored -- the case of geometry requested in an earlier session -- then select the
// group. Nothing is requested from a provider here; that is "Request geometry…", a separate action,
// and a member with nothing published is reported, not fetched.
//
// ONLY THE MEMBERS, not the documents they live in (`./groupLoadRun`, `./groupSources`): an element
// of a published node is loaded alone -- its own row, built by itself, which is what the Sources
// tab's Load does for a row -- and a source that can only be delivered whole (a mesh, a file) is
// loaded once with everything but its members hidden. This file assembles the live environment.

import { LoadCancelled, loadPrepared, NothingToDraw, parseDeliveryClaim, prepareNode, type LoadNodeDeps, type NodeRef, type PreparedNode } from "@/assets/delivery";
import type { WireDeliveryClaim } from "@/assets/types";
import { assetsApi } from "@/services/api/assets";
import { attributeSearchApi } from "@/services/api/attributeSearch";
import type { ScopeUrl } from "@/services/api/client";
import { getSingletonViewerStores } from "@/state/AdaViewerContext";
import { requestRender } from "@/state/perfStore";
import { providerName } from "@/state/providerNamesStore";
import { activeViewport } from "@/viewport/api/viewportApi";

import { memberKey, type GroupMember, type SavedGroup } from "./savedGroups";
import { runGroupLoad, type GroupLoadOutcome, type StartedLoad } from "./groupLoadRun";
import { rowsFromIndex } from "./groupSources";
import { selectSavedGroup, showOnlyMembers, whenSourcesInTree } from "./groupScene";

export type { GroupLoadOutcome } from "./groupLoadRun";

type NodeTarget = Extract<GroupMember["target"], { kind: "node" }>;

export async function loadSavedGroup(
  group: SavedGroup,
  scope: string,
  deps: LoadNodeDeps,
  onStage?: (stage: string) => void,
): Promise<GroupLoadOutcome> {
  // One delivery claim per published subject, shared by the question "per node?" and the loads.
  const claims = new Map<string, Promise<WireDeliveryClaim>>();
  const claimOf = (t: NodeTarget): Promise<WireDeliveryClaim> => {
    const key = `${t.provider}\u0000${t.collection}\u0000${t.subject}\u0000${t.revision ?? ""}`;
    let claim = claims.get(key);
    if (!claim) {
      claim = deliveryOf(scope, t);
      // Each reader handles the failure itself; this keeps an unread one from being unhandled.
      claim.catch(() => undefined);
      claims.set(key, claim);
    }
    return claim;
  };
  const out = await runGroupLoad(group, {
    onStage,
    reasonFor,
    select: selectSavedGroup,
    deliversPerNode: async (t) => (await claimOf(t)).kind === "build",
    resolveRows: (members) => resolveMemberRows(scope, members),
    startMember: (load) => startAssetNodeLoad(scope, deps, load.target, load.label, claimOf(load.target)),
    loadWhole: async (t) => {
      if (t.kind === "file") {
        const { overlay_file_in_scene } = await import("@/utils/scene/handlers/overlay_file_in_scene");
        await overlay_file_in_scene(t.source_key, undefined, { scope });
        return t.source_key;
      }
      return startAssetNodeLoad(scope, deps, t).finish();
    },
    showOnly: showOnlyMembers,
    settle: async (names) => {
      await whenSourcesInTree(names);
    },
    sceneEmpty: () => getSingletonViewerStores().useModelState.getState().loadedSourceNames.size === 0,
    // A bulk-load session that holds the camera: the three.js loader then skips its fit after each
    // model (the viewport without a three.js scene only ever frames the first model of an empty
    // scene, and the frame at the end comes after it). Imported on use: it pulls three.js in.
    holdCamera: async () => {
      const { beginBulkLoad, endBulkLoad } = await import("@/utils/scene/loadingView");
      const session = beginBulkLoad(scope, "assets:group", { hold: true, save: false });
      return () => endBulkLoad(session);
    },
    frameSelection: () => {
      activeViewport()?.fitSelection();
      requestRender();
    },
  });
  requestRender();
  return out;
}

/** Each element member's row in the published tree, by `memberKey`: from the collection's attribute
 *  index where it names the element exactly once (one request per collection, nothing opened),
 *  else by walking the Sources tree down the member's path (`resolveElementRows`, which may switch
 *  that tab to the member's collection). Members neither places are absent. */
export async function resolveMemberRows(scope: string, members: readonly GroupMember[]): Promise<Map<string, string>> {
  const out = new Map<string, string>();
  const byCollection = new Map<string, GroupMember[]>();
  for (const m of members) {
    if (m.target.kind !== "node" || m.element === null) continue;
    byCollection.set(m.target.collection, [...(byCollection.get(m.target.collection) ?? []), m]);
  }
  await Promise.all(
    [...byCollection].map(async ([collection, list]) => {
      try {
        const names = [...new Set(list.map((m) => m.element as string))];
        const answer = await attributeSearchApi.collectionNodes(scope as ScopeUrl, collection, { fullNames: names });
        if (!answer.indexed) return;
        for (const [key, row] of rowsFromIndex(list, answer.nodes)) out.set(key, row);
      } catch {
        // No index to ask: the tree walk below places what it can.
      }
    }),
  );
  const unplaced = members.filter((m) => m.target.kind === "node" && m.element !== null && !out.has(memberKey(m)));
  if (unplaced.length) {
    const { resolveElementRows } = await import("./groupAssetRows");
    for (const [key, row] of await resolveElementRows(scope, unplaced)) out.set(key, row);
  }
  return out;
}

/** The name a node's model is shown under: `label` when the caller knows it, else the node's label
 *  in the Sources tree when that collection is open there. Never the bare id -- a model's root row
 *  falls back to its own root's name, then its source name, by itself. With more than one provider
 *  naming the node, the provider is added, as the Sources tab's own Load does. */
export function nodeDisplayName(
  target: NodeTarget,
  label?: string | null,
): string | undefined {
  const node = target.node ?? target.subject;
  const browser = getSingletonViewerStores().useAssetBrowserStore.getState();
  const known = browser.collection === target.collection ? browser.forest.nodes.get(node) : undefined;
  const base = label?.trim() || known?.label?.trim();
  if (!base) return undefined;
  const several = (browser.forest.namedBy.get(node)?.size ?? 0) > 1;
  return several && !label ? `${base} · ${providerName(target.provider)}` : base;
}

/** The delivery claim AS PUBLISHED by the target's provider -- the revision pinned, or the latest
 *  when none is. Asked of the SUBJECT, the node that owns the manifest (the route reads it by its
 *  own key); `node` scopes the build below it, as the Sources tab's own load does. */
function deliveryOf(scope: string, target: NodeTarget): Promise<WireDeliveryClaim> {
  return assetsApi.getAssetDelivery(scope as ScopeUrl, target.provider, target.collection, target.subject, {
    ...(target.revision ? { revision: target.revision } : {}),
  });
}

/** Put one published node in the scene from `target.provider`'s claim -- shared by a group's load,
 *  the Scene tree's "Load from <provider>" and the scene restore, and registered with the asset
 *  browser as its own Load is. `label` names the model's root row (`nodeDisplayName`). A build
 *  that finds nothing to draw is recorded, not thrown. */
export async function loadAssetNode(
  scope: string,
  deps: LoadNodeDeps,
  target: NodeTarget,
  label?: string | null,
): Promise<void> {
  await startAssetNodeLoad(scope, deps, target, label).finish();
}

/** `loadAssetNode` in its two halves: the network half (the claim, and the build when it needs
 *  one) starts at once, so a caller loading several nodes can have the next ones building while
 *  the scene takes one model at a time; `finish()` puts it in the scene and resolves the source
 *  name it is loaded under (`null`: nothing to draw). `wire` is the subject's claim when the caller
 *  already asked for it. */
export function startAssetNodeLoad(
  scope: string,
  deps: LoadNodeDeps,
  target: NodeTarget,
  label?: string | null,
  wire?: Promise<WireDeliveryClaim>,
): StartedLoad {
  const node = target.node ?? target.subject;
  // Registered with the asset browser the way its own Load is, so the Sources tab shows it loaded
  // and "Select" resolves the members against it.
  const store = getSingletonViewerStores().useAssetBrowserStore;
  const key = `${node}\u0000${target.provider}`;
  let ref: NodeRef | null = null;
  const prepared: Promise<PreparedNode> = (async () => {
    const claim = parseDeliveryClaim(await (wire ?? deliveryOf(scope, target)));
    ref = {
      provider: target.provider,
      collection: target.collection,
      subject: target.subject,
      revision: claim.revision,
      ...(node !== target.subject ? { node } : {}),
    };
    store.getState().beginLoad(key);
    return prepareNode(deps, scope, ref, claim);
  })();
  const ready = prepared.then(
    () => undefined,
    () => undefined,
  );
  const finish = async (): Promise<string | null> => {
    try {
      const p = await prepared;
      const asset = await loadPrepared(deps, ref as unknown as NodeRef, Promise.resolve(p), nodeDisplayName(target, label));
      store.getState().endLoad(key, asset);
      return asset.sourceName;
    } catch (e) {
      // No claim to load from: the row never became busy, and there is nothing to record on it.
      if (!ref) throw e;
      if (e instanceof LoadCancelled) {
        // Stopped by request: neither loaded nor failed. Thrown on, so a caller's loop stops too.
        store.getState().cancelLoad(key);
        throw e;
      }
      // A member whose build found nothing to draw is an answer, not a failure of the group's load.
      if (e instanceof NothingToDraw) {
        store.getState().emptyLoad(key, e.message);
        return null;
      }
      store.getState().failLoad(key, reasonFor(e));
      throw e;
    }
  };
  return { ready, finish };
}

/** A load failure in words a person can act on: nothing published is the common case. */
function reasonFor(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (/\b(404|409)\b|not found|no published|delivery 'none'|nothing to deliver/i.test(msg)) {
    return `nothing published to load (${msg}) -- use Request geometry first`;
  }
  return msg;
}
